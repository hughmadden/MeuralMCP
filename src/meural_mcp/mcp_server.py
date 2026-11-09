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
    return ManagerService(root=root, config=config)


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
        return _service(storage_dir=storage_dir, config=config).assign_image(name, tmp_path)
    except KeyError as exc:
        return {"status": "failed", "reason": "device_not_found", "error": str(exc)}
    finally:
        tmp_path.unlink(missing_ok=True)


def _device_action(action: str, name: str, storage_dir=None, config=None,
                   api_url=None, api_token=None, verify_tls=None) -> dict[str, Any]:
    remote = _remote_client(api_url, api_token, verify_tls)
    if remote:
        return getattr(remote, f"{action}_device")(name)
    try:
        service = _service(storage_dir=storage_dir, config=config)
    except Exception as exc:
        return {"status": "failed", "error": str(exc)}
    try:
        if action == "sleep":
            return service.sleep(name)
        if action == "wake":
            return service.wake(name)
        if action == "clear_image":
            return service.clear_image(name)
        if action == "get_schedule":
            return service.get_schedule(name)
        if action == "clear_schedule":
            return service.clear_schedule(name)
        if action == "publish":
            return service.publish(name)
    except KeyError as exc:
        return {"status": "failed", "reason": "device_not_found", "error": str(exc)}
    return {"status": "failed", "reason": f"unknown_action_{action}"}


def mcp_get_device_schedule(name: str, **kw) -> dict[str, Any]:
    return _device_action("get_schedule", name, **kw)


def mcp_set_device_schedule(name: str, sleep_start: str, wake_time: str,
                            timezone: str = "Etc/UTC", enabled: bool = True,
                            storage_dir=None, config=None, api_url=None,
                            api_token=None, verify_tls=None) -> dict[str, Any]:
    remote = _remote_client(api_url, api_token, verify_tls)
    if remote:
        return remote.set_device_schedule(name, sleep_start, wake_time, timezone, enabled)
    try:
        return _service(storage_dir=storage_dir, config=config).set_schedule(
            name, sleep_start, wake_time, timezone, enabled)
    except KeyError as exc:
        return {"status": "failed", "reason": "device_not_found", "error": str(exc)}


def mcp_clear_device_schedule(name: str, **kw) -> dict[str, Any]:
    return _device_action("clear_schedule", name, **kw)


def mcp_clear_device_image(name: str, **kw) -> dict[str, Any]:
    return _device_action("clear_image", name, **kw)


def mcp_publish_device(name: str, **kw) -> dict[str, Any]:
    return _device_action("publish", name, **kw)


def mcp_sleep_device(name: str, **kw) -> dict[str, Any]:
    return _device_action("sleep", name, **kw)


def mcp_wake_device(name: str, **kw) -> dict[str, Any]:
    return _device_action("wake", name, **kw)


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
        """Get one device's inventory and status, including display name, product key, LAN IP, orientation, reachability, state, and assigned image."""
        return mcp_get_device_status(name)

    @server.tool()
    def get_device_image(name: str) -> dict[str, Any]:
        """Get the current assigned image reference for a device."""
        return mcp_get_device_image(name)

    @server.tool()
    def set_device_image_data(name: str, image_base64: str, filename: str = "image.png") -> dict[str, Any]:
        """Store a base64-encoded image and publish it to that frame as a one-item gallery."""
        return mcp_set_device_image_data(name, image_base64, filename)

    @server.tool()
    def clear_device_image(name: str) -> dict[str, Any]:
        """Remove the stored image for a device."""
        return mcp_clear_device_image(name)

    @server.tool()
    def publish_device(name: str) -> dict[str, Any]:
        """Re-publish the stored image and queue it for the frame (use after it drifts)."""
        return mcp_publish_device(name)

    @server.tool()
    def get_device_schedule(name: str) -> dict[str, Any]:
        """Get the device's sleep schedule."""
        return mcp_get_device_schedule(name)

    @server.tool()
    def set_device_schedule(name: str, sleep_start: str, wake_time: str,
                            timezone: str = "Etc/UTC", enabled: bool = True) -> dict[str, Any]:
        """Set a daily sleep window (HH:MM times) and the timezone it applies in."""
        return mcp_set_device_schedule(name, sleep_start, wake_time, timezone, enabled)

    @server.tool()
    def clear_device_schedule(name: str) -> dict[str, Any]:
        """Remove the device's sleep schedule."""
        return mcp_clear_device_schedule(name)

    @server.tool()
    def sleep_device(name: str) -> dict[str, Any]:
        """Suspend the frame."""
        return mcp_sleep_device(name)

    @server.tool()
    def wake_device(name: str) -> dict[str, Any]:
        """Wake the frame."""
        return mcp_wake_device(name)

    return server


def run_mcp(transport: str = "stdio") -> None:
    build_mcp_server().run(transport=transport)
