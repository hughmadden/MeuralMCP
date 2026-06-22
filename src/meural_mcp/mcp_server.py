import base64
import os
import tempfile
from pathlib import Path
from typing import Any, Callable, Optional

from .api_client import RemoteApiClient, verify_tls_from_value
from .manager import ManagerService


def _remote_client(
    api_url: Optional[str] = None,
    api_token: Optional[str] = None,
    verify_tls: Optional[bool] = None,
) -> Optional[RemoteApiClient]:
    api_url = api_url or os.getenv("MEURAL_MCP_API_URL")
    if not api_url:
        return None
    api_token = api_token or os.getenv("MEURAL_MCP_API_TOKEN")
    if not api_token:
        raise ValueError("MEURAL_MCP_API_TOKEN is required when MEURAL_MCP_API_URL is set")
    if verify_tls is None:
        verify_tls = verify_tls_from_value(os.getenv("MEURAL_MCP_API_VERIFY_TLS"))
    return RemoteApiClient(api_url, api_token, verify_tls=verify_tls)


def _service(
    storage_dir: str | Path | None = None,
    config: Optional[dict[str, Any]] = None,
    preview_writer: Optional[Callable[[dict, Path], dict]] = None,
) -> ManagerService:
    root = Path(storage_dir).expanduser() if storage_dir else None
    return ManagerService(root=root, config=config, preview_writer=preview_writer)


def mcp_list_devices(
    storage_dir: str | Path | None = None,
    config: Optional[dict[str, Any]] = None,
    api_url: Optional[str] = None,
    api_token: Optional[str] = None,
    verify_tls: Optional[bool] = None,
) -> dict[str, Any]:
    remote = _remote_client(api_url, api_token, verify_tls)
    if remote:
        return remote.summary_status()
    return _service(storage_dir=storage_dir, config=config).summary_status()


def mcp_get_device_status(
    name: str,
    storage_dir: str | Path | None = None,
    config: Optional[dict[str, Any]] = None,
    api_url: Optional[str] = None,
    api_token: Optional[str] = None,
    verify_tls: Optional[bool] = None,
) -> dict[str, Any]:
    remote = _remote_client(api_url, api_token, verify_tls)
    if remote:
        return remote.device_status(name)
    try:
        return _service(storage_dir=storage_dir, config=config).device_status(name)
    except KeyError as exc:
        return {"status": "failed", "reason": "device_not_found", "error": str(exc)}


def mcp_get_device_image(
    name: str,
    storage_dir: str | Path | None = None,
    config: Optional[dict[str, Any]] = None,
    api_url: Optional[str] = None,
    api_token: Optional[str] = None,
    verify_tls: Optional[bool] = None,
) -> dict[str, Any]:
    remote = _remote_client(api_url, api_token, verify_tls)
    if remote:
        return {"status": "ok", "device": name, "image_url": f"{remote.base_url}/devices/{name}/image"}
    try:
        service = _service(storage_dir=storage_dir, config=config)
        image = service.image_path(name)
    except KeyError as exc:
        return {"status": "failed", "reason": "device_not_found", "error": str(exc)}
    return {"status": "ok", "device": name, "image": str(image) if image else None, "exists": bool(image)}


def mcp_set_device_image(
    name: str,
    image_path: str,
    storage_dir: str | Path | None = None,
    config: Optional[dict[str, Any]] = None,
    preview_writer: Optional[Callable[[dict, Path], dict]] = None,
    api_url: Optional[str] = None,
    api_token: Optional[str] = None,
    verify_tls: Optional[bool] = None,
) -> dict[str, Any]:
    path = Path(image_path).expanduser()
    if not path.exists():
        return {"status": "failed", "reason": "image_not_found", "error": str(path)}
    remote = _remote_client(api_url, api_token, verify_tls)
    if remote:
        return remote.set_device_image(name, path)
    try:
        return _service(storage_dir=storage_dir, config=config, preview_writer=preview_writer).assign_image(name, path)
    except KeyError as exc:
        return {"status": "failed", "reason": "device_not_found", "error": str(exc)}


def mcp_set_device_image_data(
    name: str,
    image_base64: str,
    filename: str = "image.png",
    storage_dir: str | Path | None = None,
    config: Optional[dict[str, Any]] = None,
    preview_writer: Optional[Callable[[dict, Path], dict]] = None,
    api_url: Optional[str] = None,
    api_token: Optional[str] = None,
    verify_tls: Optional[bool] = None,
) -> dict[str, Any]:
    try:
        image_bytes = base64.b64decode(image_base64, validate=True)
    except Exception as exc:
        return {"status": "failed", "reason": "image_base64_invalid", "error": str(exc)}

    suffix = Path(filename).suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png"}:
        suffix = ".png"

    remote = _remote_client(api_url, api_token, verify_tls)
    if remote:
        return remote.set_device_image_bytes(name, image_bytes, suffix=suffix)

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(image_bytes)
        tmp_path = Path(tmp.name)
    try:
        return _service(storage_dir=storage_dir, config=config, preview_writer=preview_writer).assign_image(name, tmp_path)
    except KeyError as exc:
        return {"status": "failed", "reason": "device_not_found", "error": str(exc)}
    finally:
        tmp_path.unlink(missing_ok=True)


def build_mcp_server(
    *,
    streamable_http_path: str = "/mcp",
    stateless_http: bool = False,
    allowed_hosts: Optional[list[str]] = None,
):
    from mcp.server.fastmcp import FastMCP
    from mcp.server.transport_security import TransportSecuritySettings

    server = FastMCP(
        "MeuralMCP",
        streamable_http_path=streamable_http_path,
        stateless_http=stateless_http,
        transport_security=TransportSecuritySettings(allowed_hosts=allowed_hosts or []),
    )

    @server.tool()
    def list_devices() -> dict[str, Any]:
        """List all configured devices with names, display names, IPs, orientation, enabled state, reachability, and assigned image paths."""
        return mcp_list_devices()

    @server.tool()
    def get_device_status(name: str) -> dict[str, Any]:
        """Get one device's inventory and status, including display name, cloud ID, LAN IP, orientation, reachability, state, and assigned image."""
        return mcp_get_device_status(name)

    @server.tool()
    def get_device_image(name: str) -> dict[str, Any]:
        """Get the current assigned image reference for a device."""
        return mcp_get_device_image(name)

    @server.tool()
    def set_device_image(name: str, image_path: str) -> dict[str, Any]:
        """Store an image from a server-local path and load it as the current preview."""
        return mcp_set_device_image(name, image_path)

    @server.tool()
    def set_device_image_data(name: str, image_base64: str, filename: str = "image.png") -> dict[str, Any]:
        """Store a base64-encoded image uploaded by the MCP client and load it as the current preview."""
        return mcp_set_device_image_data(name, image_base64, filename)

    return server


def run_mcp(transport: str = "stdio") -> None:
    build_mcp_server().run(transport=transport)
