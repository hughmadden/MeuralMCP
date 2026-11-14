import base64
import os
import tempfile
from pathlib import Path
from typing import Any, Callable, Optional

from .api_client import RemoteApiClient, verify_tls_from_value
from .manager import ManagerService
from .modes import MenuDirection
from .video import (HostSpool, MCP_VIDEO_MAX_BYTES, MediaKind, Presentation, StopPresentation,
                    VideoTransferError, new_spool_path, playback_payload,
                    transfer_failure, upload_options)


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


DEVICE_LIST_FIELDS = (
    "name", "display_name", "enabled", "orientation", "local_ip", "image",
    "gallery_id", "reachable", "frame", "current_gallery",
    "current_mode", "modes", "controls",
)


def _device_list_entry(device: dict[str, Any]) -> dict[str, Any]:
    """Return inventory/current status without bulky historical state."""
    return {key: device.get(key) for key in DEVICE_LIST_FIELDS}


def mcp_list_devices(
    storage_dir: str | Path | None = None,
    config: Optional[dict[str, Any]] = None,
    api_url: Optional[str] = None,
    api_token: Optional[str] = None,
    verify_tls: Optional[bool] = None,
) -> dict[str, Any]:
    remote = _remote_client(api_url, api_token, verify_tls)
    if remote:
        result = remote.summary_status()
        if result.get("status") == "failed":
            return result
        inventory = remote.list_devices()
        if inventory.get("status") == "failed":
            return inventory
        result["devices"] = [_device_list_entry(device) for device in inventory.get("devices", [])]
        result["status"] = "pass"
        return result
    service = _service(storage_dir=storage_dir, config=config)
    result = service.summary_status()
    result["devices"] = [
        _device_list_entry(service.device_status(device["name"])) for device in service.devices()
    ]
    result["status"] = "pass"
    return result


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


def _feature(operation: str, name: str, storage_dir=None, config=None,
             api_url=None, api_token=None, verify_tls=None, **kwargs) -> dict[str, Any]:
    remote = _remote_client(api_url, api_token, verify_tls)
    try:
        if remote:
            return getattr(remote, operation)(name, **kwargs)
        return getattr(_service(storage_dir=storage_dir, config=config), operation)(name, **kwargs)
    except KeyError as exc:
        return {"status": "failed", "reason": "device_not_found", "error": str(exc)}
    except VideoTransferError as exc:
        return transfer_failure(exc)
    except (TypeError, ValueError) as exc:
        return {"status": "failed", "reason": "invalid_request", "error": str(exc)}


def mcp_device_time(name: str, **kw) -> dict[str, Any]:
    return _feature("device_time", name, **kw)


def mcp_get_device_modes(name: str, **kw) -> dict[str, Any]:
    return _feature("modes", name, **kw)


def mcp_register_device_mode(name: str, definition: dict[str, Any], **kw) -> dict[str, Any]:
    return _feature("register_mode", name, definition=definition, **kw)


def mcp_unregister_device_mode(name: str, mode_id: str, **kw) -> dict[str, Any]:
    return _feature("unregister_mode", name, identifier=mode_id, **kw)


def mcp_select_device_mode(name: str, mode_id: str, **kw) -> dict[str, Any]:
    return _feature("select_mode", name, identifier=mode_id, **kw)


def mcp_navigate_device_menu(name: str, direction: MenuDirection, **kw) -> dict[str, Any]:
    return _feature("navigate_menu", name, direction=direction, **kw)


def mcp_get_device_mode_events(name: str, after: int = 0, epoch: str | None = None, **kw) -> dict[str, Any]:
    return _feature("mode_events", name, after=after, epoch=epoch, **kw)


def mcp_set_device_menu_progress(name: str, mode_id: str, progress: dict[str, Any], **kw) -> dict[str, Any]:
    return _feature("set_menu_progress", name, identifier=mode_id, progress=progress, **kw)


def mcp_set_device_menu_timeout(name: str, mode_id: str, seconds: int | None,
                              menu_id: str | None = None, **kw) -> dict[str, Any]:
    return _feature("set_menu_timeout", name, identifier=mode_id, seconds=seconds, menu_id=menu_id, **kw)


def mcp_list_device_galleries(name: str, **kw) -> dict[str, Any]:
    return _feature("list_galleries", name, **kw)


def mcp_get_device_gallery(name: str, gallery_id: str, **kw) -> dict[str, Any]:
    return _feature("get_gallery", name, gallery_id=gallery_id, **kw)


def mcp_create_device_gallery(name: str, gallery_name: str, **kw) -> dict[str, Any]:
    return _feature("create_gallery", name, gallery_name=gallery_name, **kw)


def mcp_show_device_gallery(name: str, gallery_id: str, **kw) -> dict[str, Any]:
    return _feature("show_gallery", name, gallery_id=gallery_id, **kw)


def mcp_show_device_item(name: str, item_id: str, **kw) -> dict[str, Any]:
    return _feature("show_item", name, item_id=item_id, **kw)


def mcp_delete_device_gallery(name: str, gallery_id: str, **kw) -> dict[str, Any]:
    return _feature("delete_gallery", name, gallery_id=gallery_id, **kw)


def mcp_delete_device_item(name: str, item_id: str, **kw) -> dict[str, Any]:
    return _feature("delete_item", name, item_id=item_id, **kw)


def mcp_set_device_picture(name: str, path: str, timeout: int = 600, **kw) -> dict[str, Any]:
    return _feature("set_picture", name, path=path, timeout=timeout, **kw)


def mcp_get_device_settings(name: str, include_sensitive: bool = False, **kw) -> dict[str, Any]:
    return _feature("get_settings", name, include_sensitive=include_sensitive, **kw)


def mcp_get_device_settings_schema(name: str, **kw) -> dict[str, Any]:
    return _feature("get_settings_schema", name, **kw)


def mcp_update_device_settings(name: str, values: dict[str, Any], **kw) -> dict[str, Any]:
    return _feature("update_settings", name, values=values, **kw)


def mcp_run_device_action(name: str, action: str, value: Any = None, value2: Any = None, **kw) -> dict[str, Any]:
    return _feature("run_action", name, action=action, value=value, value2=value2, **kw)


def mcp_upload_device_gallery_item(
    name: str,
    gallery_id: str,
    image_base64: str,
    filename: str = "image.png",
    title: Optional[str] = None,
    storage_dir=None,
    config=None,
    api_url=None,
    api_token=None,
    verify_tls=None,
) -> dict[str, Any]:
    try:
        image_bytes = base64.b64decode(image_base64, validate=True)
    except Exception as exc:
        return {"status": "failed", "reason": "image_base64_invalid", "error": str(exc)}
    suffix = Path(filename).suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png"}:
        suffix = ".png"
    remote = _remote_client(api_url, api_token, verify_tls)
    try:
        if remote:
            return remote.upload_gallery_item(
                name, gallery_id, image_bytes=image_bytes, suffix=suffix, title=title)
        return _service(storage_dir=storage_dir, config=config).upload_gallery_item(
            name, gallery_id, data=image_bytes, filename=suffix, title=title)
    except KeyError as exc:
        return {"status": "failed", "reason": "device_not_found", "error": str(exc)}
    except (TypeError, ValueError) as exc:
        return {"status": "failed", "reason": "invalid_request", "error": str(exc)}


def mcp_get_device_video(name: str, **kw) -> dict[str, Any]:
    return _feature("video_status", name, **kw)


def mcp_get_device_video_storage(name: str, **kw) -> dict[str, Any]:
    return _feature("video_storage", name, **kw)


def mcp_list_device_video_galleries(name: str, **kw) -> dict[str, Any]:
    return _feature("list_video_galleries", name, **kw)


def mcp_create_device_video_gallery(name: str, gallery_name: str, **kw) -> dict[str, Any]:
    return _feature("create_video_gallery", name, gallery_name=gallery_name, **kw)


def mcp_get_device_video_gallery(name: str, gallery_id: str, **kw) -> dict[str, Any]:
    return _feature("get_video_gallery", name, gallery_id=gallery_id, **kw)


def mcp_delete_device_video_gallery(name: str, gallery_id: str, **kw) -> dict[str, Any]:
    return _feature("delete_video_gallery", name, gallery_id=gallery_id, **kw)


def mcp_list_device_video_files(name: str, **kw) -> dict[str, Any]:
    return _feature("list_video_files", name, **kw)


def mcp_get_device_video_file(name: str, file_id: str, **kw) -> dict[str, Any]:
    return _feature("get_video_file", name, file_id=file_id, **kw)


def mcp_delete_device_video_file(name: str, file_id: str, **kw) -> dict[str, Any]:
    return _feature("delete_video_file", name, file_id=file_id, **kw)


def mcp_play_device_video(name: str, url: str | None = None, file_id: str | None = None,
                          gallery_id: str | None = None, repeat: int = 1,
                          presentation: str = "auto", **kw) -> dict[str, Any]:
    try:
        payload = playback_payload(url=url, file_id=file_id, gallery_id=gallery_id,
                                   repeat=repeat, presentation=presentation)
    except ValueError as exc:
        return {"status": "failed", "reason": "invalid_request", "error": str(exc)}
    return _feature("play_video", name, **payload, **kw)


def mcp_stop_device_video(name: str, presentation: str = "all", **kw) -> dict[str, Any]:
    return _feature("stop_video", name, presentation=presentation, **kw)


def mcp_upload_device_video_file(name: str, gallery_id: str, video_base64: str,
                                  filename: str = "video.mp4", kind: str = "video",
                                  title: str | None = None, play: bool = False,
                                  repeat: int = 1, presentation: str = "auto", **kw) -> dict[str, Any]:
    # Bound the encoded input before allocating decoded bytes.
    if not isinstance(video_base64, str):
        return {"status": "failed", "reason": "video_base64_invalid"}
    if len(video_base64) > 4 * ((MCP_VIDEO_MAX_BYTES + 2) // 3):
        return {"status": "failed", "reason": "video_too_large", "max_bytes": MCP_VIDEO_MAX_BYTES,
                "error": "use binary REST upload for larger prepared files"}
    try:
        data = base64.b64decode(video_base64, validate=True)
    except (ValueError, TypeError) as exc:
        return {"status": "failed", "reason": "video_base64_invalid", "error": str(exc)}
    if not data or len(data) > MCP_VIDEO_MAX_BYTES:
        return {"status": "failed", "reason": "video_size_invalid", "max_bytes": MCP_VIDEO_MAX_BYTES}
    try:
        options = upload_options(filename, kind, title, play, repeat, presentation)
    except ValueError as exc:
        return {"status": "failed", "reason": "invalid_request", "error": str(exc)}
    source = None
    try:
        with HostSpool(len(data), prefix="meural-mcp-video-") as media:
            source = media.path
            media.write(data)
        return _feature("upload_video_file", name, gallery_id=gallery_id, source=source, **options, **kw)
    except VideoTransferError as exc:
        return transfer_failure(exc)
    finally:
        if source is not None:
            source.unlink(missing_ok=True)


def mcp_download_device_video_file(name: str, file_id: str, **kw) -> dict[str, Any]:
    destination = None
    try:
        destination = new_spool_path("meural-mcp-video-")
        result = _feature("download_video_file", name, file_id=file_id,
                          destination=destination, max_bytes=MCP_VIDEO_MAX_BYTES, **kw)
        if result.get("status") == "pass":
            result["video_base64"] = base64.b64encode(destination.read_bytes()).decode("ascii")
        return result
    except VideoTransferError as exc:
        return transfer_failure(exc)
    finally:
        if destination is not None:
            destination.unlink(missing_ok=True)


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
    def get_device_modes(name: str) -> dict[str, Any]:
        """List registered modes, current mode and pending menu selection. Reading never switches modes."""
        return mcp_get_device_modes(name)

    @server.tool()
    def register_device_mode(name: str, definition: dict[str, Any]) -> dict[str, Any]:
        """Register/update a declarative mode menu without restart. Registration does not activate it."""
        return mcp_register_device_mode(name, definition)

    @server.tool()
    def unregister_device_mode(name: str, mode_id: str) -> dict[str, Any]:
        """Remove an inactive external mode. Built-in Gallery is protected."""
        return mcp_unregister_device_mode(name, mode_id)

    @server.tool()
    def select_device_mode(name: str, mode_id: str) -> dict[str, Any]:
        """Explicitly select an enabled mode now, committing display ownership and closing the menu."""
        return mcp_select_device_mode(name, mode_id)

    @server.tool()
    def navigate_device_menu(name: str, direction: MenuDirection) -> dict[str, Any]:
        """Send menu input. Left/right browse mode candidates; up commits; down cancels/backs out."""
        return mcp_navigate_device_menu(name, direction)

    @server.tool()
    def get_device_mode_events(name: str, after: int = 0, epoch: str | None = None) -> dict[str, Any]:
        """Poll activation/deactivation/action events; reconcile active mode and generation on epoch/gap changes."""
        return mcp_get_device_mode_events(name, after, epoch)

    @server.tool()
    def set_device_menu_progress(name: str, mode_id: str, progress: dict[str, Any]) -> dict[str, Any]:
        """Show/update/clear loading feedback. Supply current epoch, mode_generation and optional message/percent/timeout_s, or clear=true."""
        return mcp_set_device_menu_progress(name, mode_id, progress)

    @server.tool()
    def set_device_menu_timeout(name: str, mode_id: str, seconds: int | None,
                                menu_id: str | None = None) -> dict[str, Any]:
        """Set persistent idle timeout (1–3600 seconds); null clears an override. Omit menu_id for root/picker."""
        return mcp_set_device_menu_timeout(name, mode_id, seconds, menu_id)

    @server.tool()
    def list_devices() -> dict[str, Any]:
        """List configured devices with names, IPs, orientation, enabled state, reachability, images, and current galleries."""
        return mcp_list_devices()

    @server.tool()
    def get_device_status(name: str) -> dict[str, Any]:
        """Get device status including current mode, registered modes, menu/pending selection, gallery and assigned image."""
        return mcp_get_device_status(name)

    @server.tool()
    def get_device_image(name: str) -> dict[str, Any]:
        """Get the current assigned image reference for a device."""
        return mcp_get_device_image(name)

    @server.tool()
    def set_device_image_data(name: str, image_base64: str, filename: str = "image.png") -> dict[str, Any]:
        """Store and publish image bytes: replace managed-gallery items and select the gallery/picture automatically.

        No extra publish/show calls are needed while Gallery is active. Does not change
        the active mode. Select Gallery BEFORE uploading when it should own the
        screen; activation restores its saved native selection. If uploaded while
        away, select Gallery then publish_device again.
        """
        return mcp_set_device_image_data(name, image_base64, filename)

    @server.tool()
    def clear_device_image(name: str) -> dict[str, Any]:
        """Remove the stored image for a device."""
        return mcp_clear_device_image(name)

    @server.tool()
    def publish_device(name: str) -> dict[str, Any]:
        """Re-publish the stored image directly to the frame's on-device API."""
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

    @server.tool()
    def get_device_time(name: str) -> dict[str, Any]:
        """Get the frame's current epoch time."""
        return mcp_device_time(name)

    @server.tool()
    def list_device_galleries(name: str) -> dict[str, Any]:
        """List on-device galleries and item counts."""
        return mcp_list_device_galleries(name)

    @server.tool()
    def get_device_gallery(name: str, gallery_id: str) -> dict[str, Any]:
        """Get one on-device gallery and its items."""
        return mcp_get_device_gallery(name, gallery_id)

    @server.tool()
    def create_device_gallery(name: str, gallery_name: str) -> dict[str, Any]:
        """Create an on-device gallery."""
        return mcp_create_device_gallery(name, gallery_name)

    @server.tool()
    def upload_device_gallery_item(name: str, gallery_id: str, image_base64: str,
                                   filename: str = "image.png", title: Optional[str] = None) -> dict[str, Any]:
        """Add image bytes without selecting gallery/item or changing mode. Select Gallery before explicit show calls when needed."""
        return mcp_upload_device_gallery_item(name, gallery_id, image_base64, filename, title)

    @server.tool()
    def show_device_gallery(name: str, gallery_id: str) -> dict[str, Any]:
        """Make an on-device gallery current."""
        return mcp_show_device_gallery(name, gallery_id)

    @server.tool()
    def show_device_item(name: str, item_id: str) -> dict[str, Any]:
        """Display one on-device item."""
        return mcp_show_device_item(name, item_id)

    @server.tool()
    def delete_device_gallery(name: str, gallery_id: str) -> dict[str, Any]:
        """Delete an on-device gallery and its links; items are kept by the frame."""
        return mcp_delete_device_gallery(name, gallery_id)

    @server.tool()
    def delete_device_item(name: str, item_id: str) -> dict[str, Any]:
        """Delete an on-device item, its file, and links."""
        return mcp_delete_device_item(name, item_id)

    @server.tool()
    def set_device_picture(name: str, path: str, timeout: int = 600) -> dict[str, Any]:
        """Display a frame-local image path now; timeout 0 keeps it until replaced."""
        return mcp_set_device_picture(name, path, timeout)

    @server.tool()
    def get_device_settings(name: str, include_sensitive: bool = False) -> dict[str, Any]:
        """Read settings/hardware; credential-like values are redacted unless explicitly included."""
        return mcp_get_device_settings(name, include_sensitive)

    @server.tool()
    def get_device_settings_schema(name: str) -> dict[str, Any]:
        """Get settable frame columns and their types."""
        return mcp_get_device_settings_schema(name)

    @server.tool()
    def update_device_settings(name: str, values: dict[str, Any]) -> dict[str, Any]:
        """Write one or more frame settings, returning applied/rejected/unknown fields."""
        return mcp_update_device_settings(name, values)

    @server.tool()
    def run_device_action(name: str, action: str, value: Any = None, value2: Any = None) -> dict[str, Any]:
        """Run a frame hardware action; network, power, and input actions may be disruptive."""
        return mcp_run_device_action(name, action, value, value2)

    @server.tool()
    def get_device_video(name: str) -> dict[str, Any]:
        """Get video capabilities, current fullscreen/overlay layers and disk storage."""
        return mcp_get_device_video(name)

    @server.tool()
    def get_device_video_storage(name: str) -> dict[str, Any]:
        """Get free, reserved and available upload bytes for persistent video files."""
        return mcp_get_device_video_storage(name)

    @server.tool()
    def list_device_video_galleries(name: str) -> dict[str, Any]:
        """List persistent video galleries."""
        return mcp_list_device_video_galleries(name)

    @server.tool()
    def create_device_video_gallery(name: str, gallery_name: str) -> dict[str, Any]:
        """Create a persistent gallery for prepared media."""
        return mcp_create_device_video_gallery(name, gallery_name)

    @server.tool()
    def get_device_video_gallery(name: str, gallery_id: str) -> dict[str, Any]:
        """Get video gallery metadata and its owned files."""
        return mcp_get_device_video_gallery(name, gallery_id)

    @server.tool()
    def delete_device_video_gallery(name: str, gallery_id: str) -> dict[str, Any]:
        """Delete a video gallery and its files; stop active playback first (409 if in use)."""
        return mcp_delete_device_video_gallery(name, gallery_id)

    @server.tool()
    def list_device_video_files(name: str) -> dict[str, Any]:
        """List persistent prepared-media file metadata."""
        return mcp_list_device_video_files(name)

    @server.tool()
    def get_device_video_file(name: str, file_id: str) -> dict[str, Any]:
        """Get one prepared-media file's metadata."""
        return mcp_get_device_video_file(name, file_id)

    @server.tool()
    def delete_device_video_file(name: str, file_id: str) -> dict[str, Any]:
        """Delete a prepared-media file; stop active playback first (409 if in use)."""
        return mcp_delete_device_video_file(name, file_id)

    @server.tool()
    def upload_device_video_file(name: str, gallery_id: str, video_base64: str,
                                  filename: str = "video.mp4", kind: MediaKind = "video",
                                  title: str | None = None, play: bool = False,
                                  repeat: int = 1, presentation: Presentation = "auto") -> dict[str, Any]:
        """Upload prepared bytes (16 MiB maximum); prefer binary REST for larger files. No conversion."""
        return mcp_upload_device_video_file(name, gallery_id, video_base64, filename, kind,
                                           title, play, repeat, presentation)

    @server.tool()
    def download_device_video_file(name: str, file_id: str) -> dict[str, Any]:
        """Download prepared bytes as base64 (16 MiB maximum); use binary REST for larger files."""
        return mcp_download_device_video_file(name, file_id)

    @server.tool()
    def play_device_video(name: str, url: str | None = None, file_id: str | None = None,
                          gallery_id: str | None = None, repeat: int = 1,
                          presentation: Presentation = "auto") -> dict[str, Any]:
        """Play exactly one prepared file/gallery or HTTP(S)/RTSP(S) source (alpha: HTTP(S)). Repeat: 1 once, 0 forever, N cycles."""
        return mcp_play_device_video(name, url, file_id, gallery_id, repeat, presentation)

    @server.tool()
    def stop_device_video(name: str, presentation: StopPresentation = "all") -> dict[str, Any]:
        """Stop all video or just fullscreen/overlay; fullscreen restores the original gallery/item."""
        return mcp_stop_device_video(name, presentation)

    return server


def run_mcp(transport: str = "stdio") -> None:
    build_mcp_server().run(transport=transport)
