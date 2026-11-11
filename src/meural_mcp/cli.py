import argparse
import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

from .manager import (
    ManagerService,
    canonical_name,
    load_config,
    normalise_orientation,
    save_config,
    storage_dir,
)

SERVICE_DIR = Path(__file__).resolve().parents[2] / "service"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MeuralMCP — local Meural frame manager")
    parser.add_argument("--storage-dir", type=Path, default=None, help="config/state directory")
    parser.add_argument("--env-file", type=Path, default=None, help="local env file")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="create config with a generated API token")
    init.add_argument("--api-token", default=None)

    add = sub.add_parser("add-device", help="register a frame")
    add.add_argument("--name", required=True)
    add.add_argument("--ip", required=True, help="frame LAN address")
    add.add_argument("--orientation", default="landscape", choices=["landscape", "portrait"])
    add.add_argument("--display-name", default=None)
    add.add_argument("--product-key", default=None, help="frame product key (discovered if omitted)")
    add.add_argument("--token", default=None,
                     help="frame API token from `uf.sh token` (enables the on-device API)")

    sub.add_parser("service-install", help="install the local API service (needs root)")
    sub.add_parser("publish-all", help="re-publish stored images for every enabled device")

    sub.add_parser("daemon", help="sleep schedules and optional periodic re-publish")

    serve = sub.add_parser("serve", help="run the REST API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8733)

    mcp = sub.add_parser("mcp", help="run the MCP server")
    mcp.add_argument("--transport", default="stdio", choices=["stdio", "streamable-http"])

    return parser


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def print_result(result: dict) -> None:
    print(json.dumps(result, indent=2, default=str))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.storage_dir or storage_dir()
    load_env_file(args.env_file or root / "meural-mcp.env")
    os.environ.setdefault("MEURAL_MCP_STORAGE_DIR", str(root))

    if args.command == "init":
        root.mkdir(parents=True, exist_ok=True)
        config = load_config(root)
        token = args.api_token or secrets.token_urlsafe(24)
        config["api_token"] = token
        save_config(config, root)
        config_path = root / "config.json"
        print(f"wrote {config_path}", file=sys.stderr)
        print("Add frames with: meural-mcp add-device --name canvas-1 --ip <ip> --orientation <layout> "
              "--token <frame api token>", file=sys.stderr)
        print_result({"config_path": str(config_path), "api_token": token})
        return 0

    if args.command == "add-device":
        config = load_config(root)
        name = canonical_name(args.name)
        config["devices"] = [d for d in config["devices"] if d["name"] != name]
        config["devices"].append({
            "name": name,
            "display_name": args.display_name or args.name,
            "local_ip": args.ip,
            "product_key": args.product_key,
            "orientation": normalise_orientation(args.orientation),
            "enabled": True,
            "api_token": args.token,
        })
        save_config(config, root)
        print_result({"added": name, "ip": args.ip,
                      "orientation": normalise_orientation(args.orientation),
                      "api_token": bool(args.token)})
        return 0

    if args.command == "service-install":
        script = SERVICE_DIR / "meural-api-install"
        if not script.exists():
            print(f"missing {script}", file=sys.stderr)
            return 2
        return subprocess.call(["sh", str(script)])

    if args.command == "publish-all":
        service = ManagerService(root=root)
        results = {}
        for device in service.devices():
            if device.get("enabled", True):
                try:
                    results[device["name"]] = service.publish(device["name"])
                except Exception as exc:
                    results[device["name"]] = {"status": "failed", "error": str(exc)}
        print_result(results)
        return 0

    if args.command == "daemon":
        ManagerService(root=root).run_daemon()
        return 0

    if args.command == "serve":
        import uvicorn

        uvicorn.run("meural_mcp.api:app", host=args.host, port=args.port)
        return 0

    if args.command == "mcp":
        from .mcp_server import run_mcp

        run_mcp(transport=args.transport)
        return 0

    return 2
