import argparse
import os
import secrets
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from .cloud import MeuralCloudClient
from .manager import (
    ManagerService,
    ensure_blank_gallery_assigned,
    initialise_cloud_timeouts,
    load_config,
    save_config,
    storage_dir,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MeuralMCP preview manager")
    parser.add_argument("--storage-dir", type=Path, default=None, help="config/state directory")
    parser.add_argument("--env-file", type=Path, default=None, help="local env file")
    sub = parser.add_subparsers(dest="command", required=True)

    init_local = sub.add_parser("init-local", help="write generic local config/env only")
    init_local.add_argument("--api-token", required=True)

    init_cloud = sub.add_parser("init-cloud", help="write config/env, prepare blank gallery, set timeouts, and sync")
    init_cloud.add_argument("--username", required=True)
    init_cloud.add_argument("--password", required=True)
    init_cloud.add_argument("--api-token", default=None, help="REST API token; generated if omitted")
    init_cloud.add_argument("--install-systemd", action="store_true", help="install and start a user systemd daemon")
    init_cloud.add_argument("--no-systemd-prompt", action="store_true", help="do not prompt for user systemd install")

    sub.add_parser("daemon", help="run quiet preview manager loop")

    serve = sub.add_parser("serve", help="run REST service")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8733)

    mcp = sub.add_parser("mcp", help="run MCP server for coding agents")
    mcp.add_argument("--transport", default="stdio", choices=["stdio", "streamable-http"])

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.storage_dir or storage_dir()
    env_file = args.env_file or root / "meural-mcp.env"

    if args.command == "init-local":
        init_result = initialise_config_file(root, args.api_token)
        if init_result["backup"]:
            print(f"warning: existing config backed up to {init_result['backup']} before writing new init values", file=sys.stderr)
        print_result({"storage_dir": str(root), "config_path": init_result["config_path"], "backup": init_result["backup"], "api_token": args.api_token, "cloud": None})
        return 0

    if args.command == "init-cloud":
        os.environ["MEURAL_MCP_STORAGE_DIR"] = str(root)
        cloud = MeuralCloudClient(args.username, args.password)
        result = run_init_cloud(root, cloud, api_token=args.api_token)
        if result["backup"]:
            print(f"warning: existing config backed up to {result['backup']} before writing new init values", file=sys.stderr)
        print(f"wrote {result['config_path']}; edit it if you need to change device names, IDs, IPs, or enabled flags", file=sys.stderr)
        print(f"REST API token: {result['api_token']}", file=sys.stderr)
        print("Save this token for REST clients; it is also stored in config.json.", file=sys.stderr)
        if should_install_systemd(args):
            unit = install_systemd_user_service(root)
            print(f"installed user systemd service at {unit}", file=sys.stderr)
        print_result(result)
        return 0

    load_env_file(env_file)
    os.environ.setdefault("MEURAL_MCP_STORAGE_DIR", str(root))

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


def initialise_config_file(root: Path, api_token: str) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    config_path = root / "config.json"
    backup = None
    if config_path.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = root / f"config.{stamp}.bak.json"
        shutil.copy2(config_path, backup_path)
        backup = str(backup_path)
    config = load_config(root)
    config.pop("cloud", None)
    config["api_token"] = api_token
    save_config(config, root)
    return {"config_path": str(config_path), "backup": backup}


def run_init_cloud(root: Path, cloud: MeuralCloudClient, api_token: str | None = None) -> dict:
    api_token = api_token or secrets.token_urlsafe(32)
    init_result = initialise_config_file(root, api_token)
    config = load_config(root)
    discover = discover_devices_from_cloud(cloud)
    config["devices"] = discover["devices"]
    refresh = {"refreshed": [device["name"] for device in config["devices"] if device.get("cloud_id")]}
    blank_galleries = ensure_blank_gallery_assigned(cloud, config, root)
    save_config(config, root)
    timeouts = initialise_cloud_timeouts(cloud, config, include_disabled=True)
    return {
        "storage_dir": str(root),
        "config_path": init_result["config_path"],
        "backup": init_result["backup"],
        "api_token": api_token,
        "cloud": {
            "discovered": discover,
            "refresh": refresh,
            "blank_galleries": blank_galleries,
            "timeouts": timeouts,
        },
    }


def discover_devices_from_cloud(cloud: MeuralCloudClient) -> dict:
    response = cloud.list_devices()
    devices = []
    for index, device in enumerate(response.get("data", []), start=1):
        display_name = device.get("alias") or f"Canvas {index}"
        devices.append(
            {
                "name": slugify(display_name) or f"canvas-{index}",
                "display_name": display_name,
                "cloud_id": device.get("id"),
                "local_ip": device.get("localIp"),
                "orientation": device.get("orientation") or "landscape",
                "enabled": True,
            }
        )
    return {"count": len(devices), "devices": devices}


def slugify(value: str) -> str:
    import re

    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower())
    return slug.strip("-")


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def should_install_systemd(args) -> bool:
    if getattr(args, "install_systemd", False):
        return True
    if getattr(args, "no_systemd_prompt", False) or not sys.stdin.isatty():
        return False
    answer = input("Install and start the MeuralMCP user systemd daemon now? [y/N] ").strip().lower()
    return answer in {"y", "yes"}


def install_systemd_user_service(root: Path, executable: Optional[str] = None, run_systemctl: bool = True) -> str:
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    unit_path = unit_dir / "meural-mcp.service"
    command = executable or f"{sys.executable} -m meural_mcp.cli --storage-dir {root} daemon"
    unit_path.write_text(
        "\n".join(
            [
                "[Unit]",
                "Description=MeuralMCP LAN preview manager",
                "After=network-online.target",
                "",
                "[Service]",
                "Type=simple",
                f"ExecStart={command}",
                "Restart=always",
                "RestartSec=15",
                "",
                "[Install]",
                "WantedBy=default.target",
                "",
            ]
        )
    )
    if run_systemctl:
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
        subprocess.run(["systemctl", "--user", "enable", "--now", unit_path.name], check=False)
    return str(unit_path)


def print_result(result: dict) -> None:
    import json

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
