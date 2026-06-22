from pathlib import Path
from typing import Any, Callable, Optional

from .manager import ManagerService


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
) -> dict[str, Any]:
    return _service(storage_dir=storage_dir, config=config).summary_status()


def mcp_get_device_status(
    name: str,
    storage_dir: str | Path | None = None,
    config: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    try:
        return _service(storage_dir=storage_dir, config=config).device_status(name)
    except KeyError as exc:
        return {"status": "failed", "reason": "device_not_found", "error": str(exc)}


def mcp_get_device_image(
    name: str,
    storage_dir: str | Path | None = None,
    config: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
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
) -> dict[str, Any]:
    path = Path(image_path).expanduser()
    if not path.exists():
        return {"status": "failed", "reason": "image_not_found", "error": str(path)}
    try:
        return _service(storage_dir=storage_dir, config=config, preview_writer=preview_writer).assign_image(name, path)
    except KeyError as exc:
        return {"status": "failed", "reason": "device_not_found", "error": str(exc)}


def build_mcp_server():
    from mcp.server.fastmcp import FastMCP

    server = FastMCP("MeuralMCP")

    @server.tool()
    def list_devices() -> dict[str, Any]:
        return mcp_list_devices()

    @server.tool()
    def get_device_status(name: str) -> dict[str, Any]:
        return mcp_get_device_status(name)

    @server.tool()
    def get_device_image(name: str) -> dict[str, Any]:
        return mcp_get_device_image(name)

    @server.tool()
    def set_device_image(name: str, image_path: str) -> dict[str, Any]:
        """Store an image for a device and load it as the current preview."""
        return mcp_set_device_image(name, image_path)

    return server


def run_mcp(transport: str = "stdio") -> None:
    build_mcp_server().run(transport=transport)
