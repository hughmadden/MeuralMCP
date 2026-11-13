"""Device registry, image store, and direct on-device gallery publishing."""

from __future__ import annotations

import json
import os
import struct
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

import requests

from .local import FrameLocalClient, FrameLocalError
from .modes import direction as menu_direction, event_cursor, menu_timeout, mode_definition, mode_id
from .video import (VideoTransferError, playback_payload, resource_id,
                    upload_options, validate_presentation)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_name(name: str) -> str:
    return name.strip().lower().replace("_", "-").replace(" ", "-")


def storage_dir() -> Path:
    return Path(os.getenv("MEURAL_MCP_STORAGE_DIR", "~/.config/meural-mcp")).expanduser()


def default_config() -> dict[str, Any]:
    return {
        "poll_seconds": 60,
        "reload_after_seconds": 0,
        "devices": [],
    }


def load_config(root: Optional[Path] = None) -> dict[str, Any]:
    root = root or storage_dir()
    path = root / "config.json"
    if not path.exists():
        root.mkdir(parents=True, exist_ok=True)
        config = default_config()
        path.write_text(json.dumps(config, indent=2))
        return config
    loaded = json.loads(path.read_text())
    config = default_config()
    config.update({key: value for key, value in loaded.items() if key not in {"devices", "service"}})
    devices: dict[str, dict] = {}
    for device in loaded.get("devices", []):
        name = canonical_name(device["name"])
        merged = devices.get(name, {"name": name})
        merged.update(device)
        merged["name"] = name
        devices[name] = merged
    config["devices"] = list(devices.values())
    return config


def save_config(config: dict[str, Any], root: Optional[Path] = None) -> None:
    root = root or storage_dir()
    root.mkdir(parents=True, exist_ok=True)
    (root / "config.json").write_text(json.dumps(config, indent=2))


def load_state(root: Path) -> dict[str, Any]:
    path = root / "state.json"
    if not path.exists():
        return {"devices": {}}
    try:
        return json.loads(path.read_text())
    except ValueError:
        return {"devices": {}}


def save_state(root: Path, state: dict[str, Any]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "state.json").write_text(json.dumps(state, indent=2))


def auth_token_valid(expected: Optional[str], authorization: Optional[str], x_token: Optional[str]) -> bool:
    if not expected:
        return False
    if x_token and x_token == expected:
        return True
    if authorization and authorization.startswith("Bearer "):
        return authorization.removeprefix("Bearer ").strip() == expected
    return False


def image_dimensions(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        if len(data) < 24:
            raise ValueError("invalid PNG")
        return struct.unpack(">II", data[16:24])
    if data.startswith(b"\xff\xd8"):
        i = 2
        while i < len(data) - 9:
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3):
                height, width = struct.unpack(">HH", data[i + 5:i + 9])
                return width, height
            if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
        raise ValueError("invalid JPEG")
    raise ValueError("unsupported image format")


def orientation_for_dimensions(width: int, height: int) -> str:
    return "portrait" if height > width else "landscape"


def normalise_orientation(value: Optional[str]) -> str:
    aliases = {"horizontal": "landscape", "landscape": "landscape", "vertical": "portrait", "portrait": "portrait"}
    return aliases.get((value or "").strip().lower(), "landscape")


class ManagerService:
    def __init__(
        self,
        root: Optional[Path] = None,
        config: Optional[dict[str, Any]] = None,
        local_client_factory: Optional[Callable[[str, int], FrameLocalClient]] = None,
        now_provider: Optional[Callable[[], datetime]] = None,
    ):
        self.root = root or storage_dir()
        self.config = config or load_config(self.root)
        self.local_client_factory = local_client_factory or (lambda ip, timeout=5: FrameLocalClient(ip, timeout=timeout))
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self.images_dir.mkdir(parents=True, exist_ok=True)

    # ---- paths -----------------------------------------------------------
    @property
    def images_dir(self) -> Path:
        return self.root / "images"

    # ---- devices ---------------------------------------------------------
    def devices(self) -> list[dict]:
        return self.config.get("devices", [])

    def device(self, name: str) -> dict:
        target = canonical_name(name)
        for device in self.devices():
            aliases = {canonical_name(device["name"]), canonical_name(device.get("display_name", ""))}
            if target in aliases:
                return device
        raise KeyError(f"unknown device: {name}")

    def image_path(self, name: str) -> Optional[Path]:
        device = self.device(name)
        for ext in (".jpg", ".jpeg", ".png"):
            path = self.images_dir / f"{device['name']}{ext}"
            if path.exists():
                return path
        return None

    def state(self) -> dict[str, Any]:
        return load_state(self.root)

    def save_state(self, state: dict[str, Any]) -> None:
        save_state(self.root, state)

    # ---- publishing ------------------------------------------------------
    def _publish_to_device(
        self, client: FrameLocalClient, device: dict, image: Path
    ) -> dict[str, Any]:
        """Publish a one-item gallery through the frame's authenticated API."""
        state = self.state()
        entry = state.setdefault("devices", {}).setdefault(device["name"], {})
        gid = entry.get("gallery_id")
        if not gid or not client.gallery_exists(gid):
            gid = client.create_gallery(f"MeuralMCP {device['name']}")
            entry["gallery_id"] = gid
            self.save_state(state)

        item_id = client.upload_item(gid, image, title=device.get("display_name") or device["name"])
        delete_failed = []
        for other in client.gallery_items(gid):
            if str(other.get("id")) != str(item_id):
                try:
                    client.delete_item(other["id"])
                except Exception:
                    delete_failed.append(other.get("id"))
        client.change_gallery(gid)
        client.change_item(item_id)
        if delete_failed:
            raise RuntimeError(f"delete failed for stale item(s): {delete_failed}")
        return {"status": "pass", "gallery_id": gid, "device": True}

    def publish(self, name: str) -> dict[str, Any]:
        """Upload the stored image, remove stale items, and show the new item."""
        device = self.device(name)
        image = self.image_path(name)
        if image is None:
            return {"status": "skipped", "reason": "no_image"}
        return self._on_device_call(
            name,
            lambda client: self._publish_to_device(client, device, image),
            timeout=60,
        )

    # ---- image writes ----------------------------------------------------
    def set_image(self, name: str, data: bytes, filename: Optional[str] = None) -> dict[str, Any]:
        device = self.device(name)
        if not device.get("enabled", True):
            return {"status": "failed", "reason": "disabled"}

        suffix = Path(filename or "upload.jpg").suffix.lower()
        suffix = suffix if suffix in (".jpg", ".jpeg", ".png") else ".jpg"
        temp = self.images_dir / f".upload-{device['name']}{suffix}"
        temp.write_bytes(data)

        try:
            width, height = image_dimensions(temp)
        except ValueError:
            temp.unlink(missing_ok=True)
            return {"status": "failed", "reason": "unreadable_image"}

        found = orientation_for_dimensions(width, height)
        expected = normalise_orientation(device.get("orientation"))
        if found != expected:
            temp.unlink(missing_ok=True)
            return {"status": "failed", "reason": "orientation_mismatch", "expected": expected, "found": found, "width": width, "height": height}

        for ext in (".jpg", ".jpeg", ".png"):
            (self.images_dir / f"{device['name']}{ext}").unlink(missing_ok=True)
        target = self.images_dir / f"{device['name']}{suffix}"
        temp.replace(target)

        published = self.publish(device["name"])
        state = self.state()
        state.setdefault("devices", {}).setdefault(device["name"], {}).update(
            {"last_image_at": now_iso(), "last_error": None, "image": str(target)}
        )
        self.save_state(state)
        return {"status": published["status"], "image": str(target), "gallery_id": published.get("gallery_id")}

    def clear_image(self, name: str) -> dict[str, Any]:
        device = self.device(name)
        image = self.image_path(device["name"])
        if image is None:
            return {"status": "skipped", "reason": "no_image"}
        image.unlink()
        return {"status": "pass"}

    # ---- device actions --------------------------------------------------
    def _client(self, device: dict, timeout: int = 5) -> FrameLocalClient:
        ip = device.get("local_ip")
        if not ip:
            raise TimeoutError("no local IP for device")
        client = self.local_client_factory(ip, timeout)
        token = device.get("api_token")
        if token:
            client.token = token          # drives the :8099 token transport
        return client

    # ---- on-device API features ------------------------------------------
    def _on_device_call(
        self,
        name: str,
        call: Callable[[FrameLocalClient], Any],
        *,
        timeout: int = 5,
        result_key: str = "result",
    ) -> dict[str, Any]:
        """Run an authenticated frame call and normalise its result envelope."""
        device = self.device(name)
        if not device.get("api_token"):
            return {"status": "failed", "reason": "on_device_api_required"}
        response = call(self._client(device, timeout))
        if isinstance(response, dict):
            if response.get("is_ok") is False:
                return {**response, "status": "failed", "reason": "on_device_api_failed"}
            # Validation failures produced inside a call are already envelopes.
            if response.get("status") == "failed":
                return response
            return {**response, "status": "pass"}
        return {"status": "pass", result_key: response}

    # ---- checked frame transport -----------------------------------------
    def _checked_device_call(self, name: str, call: Callable[[FrameLocalClient], Any],
                    *, timeout: int = 30) -> dict[str, Any]:
        try:
            return self._on_device_call(name, call, timeout=timeout)
        except FrameLocalError as exc:
            try:
                details = json.loads(exc.body)
            except ValueError:
                details = {}
            if not isinstance(details, dict):
                details = {}
            return {**details, "status": "failed", "is_ok": False,
                    "reason": details.get("reason", "on_device_api_failed"),
                    "http_status": exc.status}
        except VideoTransferError as exc:
            return {"status": "failed", "is_ok": False, "reason": "transfer_failed",
                    "error": str(exc), "http_status": exc.http_status}
        except requests.RequestException:
            # Request exceptions may include credential-bearing URLs.
            return {"status": "failed", "is_ok": False,
                    "reason": "device_unavailable", "http_status": 502}

    # ---- modes and menu input --------------------------------------------
    def modes(self, name: str) -> dict[str, Any]:
        return self._checked_device_call(name, lambda client: client.modes())

    def register_mode(self, name: str, definition: dict) -> dict[str, Any]:
        mode_definition(definition)
        return self._checked_device_call(name, lambda client: client.register_mode(definition))

    def unregister_mode(self, name: str, identifier: str) -> dict[str, Any]:
        mode_id(identifier)
        return self._checked_device_call(name, lambda client: client.unregister_mode(identifier))

    def select_mode(self, name: str, identifier: str) -> dict[str, Any]:
        mode_id(identifier)
        return self._checked_device_call(name, lambda client: client.select_mode(identifier))

    def navigate_menu(self, name: str, direction: str) -> dict[str, Any]:
        menu_direction(direction)
        return self._checked_device_call(name, lambda client: client.navigate_menu(direction))

    def mode_events(self, name: str, after: int = 0, epoch: str | None = None) -> dict[str, Any]:
        event_cursor(after, epoch)
        return self._checked_device_call(name, lambda client: client.mode_events(after, epoch))

    def set_menu_timeout(self, name: str, identifier: str, seconds: int | None,
                         menu_id: str | None = None) -> dict[str, Any]:
        mode_id(identifier)
        menu_timeout(seconds, menu_id)
        return self._checked_device_call(name, lambda client: client.set_menu_timeout(identifier, seconds, menu_id))

    # ---- prepared video: forwarding only, never conversion ----------------

    def video_status(self, name: str) -> dict[str, Any]:
        return self._checked_device_call(name, lambda client: client.video_status())

    def video_storage(self, name: str) -> dict[str, Any]:
        return self._checked_device_call(name, lambda client: client.video_storage())

    def list_video_galleries(self, name: str) -> dict[str, Any]:
        return self._checked_device_call(name, lambda client: client.list_video_galleries())

    def create_video_gallery(self, name: str, gallery_name: str) -> dict[str, Any]:
        if not isinstance(gallery_name, str) or not gallery_name.strip():
            raise ValueError("gallery_name must be nonempty")
        return self._checked_device_call(name, lambda client: client.create_video_gallery(gallery_name))

    def get_video_gallery(self, name: str, gallery_id: str) -> dict[str, Any]:
        resource_id(gallery_id)
        return self._checked_device_call(name, lambda client: client.get_video_gallery(gallery_id))

    def delete_video_gallery(self, name: str, gallery_id: str) -> dict[str, Any]:
        resource_id(gallery_id)
        return self._checked_device_call(name, lambda client: client.delete_video_gallery(gallery_id))

    def list_video_files(self, name: str) -> dict[str, Any]:
        return self._checked_device_call(name, lambda client: client.list_video_files())

    def get_video_file(self, name: str, file_id: str) -> dict[str, Any]:
        resource_id(file_id)
        return self._checked_device_call(name, lambda client: client.get_video_file(file_id))

    def delete_video_file(self, name: str, file_id: str) -> dict[str, Any]:
        resource_id(file_id)
        return self._checked_device_call(name, lambda client: client.delete_video_file(file_id))

    def upload_video_file(self, name: str, gallery_id: str, source: Path,
                          filename: str = "video.mp4", kind: str = "video",
                          title: str | None = None, play: bool = False,
                          repeat: int = 1, presentation: str = "auto") -> dict[str, Any]:
        resource_id(gallery_id)
        options = upload_options(filename, kind, title, play, repeat, presentation)
        if not source.is_file() or not source.stat().st_size:
            raise ValueError("prepared media must be a nonempty file")
        return self._checked_device_call(name, lambda client: client.upload_video_file(
            gallery_id, source, **options), timeout=300)

    def download_video_file(self, name: str, file_id: str, destination: Path,
                            max_bytes: int | None = None) -> dict[str, Any]:
        resource_id(file_id)
        return self._checked_device_call(name, lambda client: client.download_video_file(
            file_id, destination, max_bytes=max_bytes), timeout=300)

    def play_video(self, name: str, url: str | None = None, file_id: str | None = None,
                   gallery_id: str | None = None, repeat: int = 1,
                   presentation: str = "auto") -> dict[str, Any]:
        payload = playback_payload(url=url, file_id=file_id, gallery_id=gallery_id,
                                   repeat=repeat, presentation=presentation)
        return self._checked_device_call(name, lambda client: client.play_video(**payload))

    def stop_video(self, name: str, presentation: str = "all") -> dict[str, Any]:
        validate_presentation(presentation, stop=True)
        return self._checked_device_call(name, lambda client: client.stop_video(presentation))

    def device_time(self, name: str) -> dict[str, Any]:
        return self._on_device_call(name, lambda client: client.time(), timeout=15)

    def list_galleries(self, name: str) -> dict[str, Any]:
        return self._on_device_call(name, lambda client: client.list_galleries(), timeout=20)

    def get_gallery(self, name: str, gallery_id: str | int) -> dict[str, Any]:
        return self._on_device_call(
            name, lambda client: client.gallery(gallery_id), timeout=20
        )

    def create_gallery(self, name: str, gallery_name: str) -> dict[str, Any]:
        if not isinstance(gallery_name, str) or not gallery_name.strip():
            return {"status": "failed", "reason": "invalid_gallery_name"}
        return self._on_device_call(
            name,
            lambda client: client.create_gallery(gallery_name),
            timeout=30,
            result_key="gallery_id",
        )

    def upload_gallery_item(
        self,
        name: str,
        gallery_id: str | int,
        data: bytes,
        filename: Optional[str] = None,
        title: Optional[str] = None,
    ) -> dict[str, Any]:
        suffix = Path(filename).suffix.lower() if filename else ""
        if suffix not in {".jpg", ".jpeg", ".png"}:
            suffix = ".png" if data.startswith(b"\x89PNG\r\n\x1a\n") else ".jpg"

        def upload(client: FrameLocalClient) -> Any:
            temp_path: Optional[Path] = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="wb",
                    prefix=".gallery-upload-",
                    suffix=suffix,
                    dir=self.images_dir,
                    delete=False,
                ) as temp:
                    temp_path = Path(temp.name)
                    temp.write(data)
                try:
                    image_dimensions(temp_path)
                except (ValueError, struct.error):
                    return {"status": "failed", "reason": "unreadable_image"}
                return client.upload_item(gallery_id, temp_path, title=title)
            finally:
                if temp_path is not None:
                    temp_path.unlink(missing_ok=True)

        return self._on_device_call(
            name, upload, timeout=60, result_key="item_id"
        )

    def show_gallery(self, name: str, gallery_id: str | int) -> dict[str, Any]:
        return self._on_device_call(
            name, lambda client: client.change_gallery(gallery_id), timeout=30
        )

    def show_item(self, name: str, item_id: str | int) -> dict[str, Any]:
        return self._on_device_call(
            name, lambda client: client.change_item(item_id), timeout=30
        )

    def delete_gallery(self, name: str, gallery_id: str | int) -> dict[str, Any]:
        return self._on_device_call(
            name, lambda client: client.delete_gallery(gallery_id), timeout=30
        )

    def delete_item(self, name: str, item_id: str | int) -> dict[str, Any]:
        return self._on_device_call(
            name, lambda client: client.delete_item(item_id), timeout=30
        )

    def set_picture(self, name: str, path: str, timeout: int = 600) -> dict[str, Any]:
        if not isinstance(path, str) or not path.strip():
            return {"status": "failed", "reason": "invalid_path"}
        if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout < 0:
            return {"status": "failed", "reason": "invalid_timeout"}
        return self._on_device_call(
            name,
            lambda client: client.set_picture(path, timeout=timeout),
            timeout=max(60, timeout + 10),
        )

    @staticmethod
    def _redact_settings(value: Any) -> Any:
        if isinstance(value, dict):
            redacted = {}
            for key, item in value.items():
                normalised = str(key).lower()
                sensitive = normalised == "captive_user_value" or any(
                    marker in normalised for marker in ("pass", "password", "token", "secret")
                )
                redacted[key] = "<redacted>" if sensitive else ManagerService._redact_settings(item)
            return redacted
        if isinstance(value, list):
            return [ManagerService._redact_settings(item) for item in value]
        return value

    def get_settings(self, name: str, include_sensitive: bool = False) -> dict[str, Any]:
        result = self._on_device_call(name, lambda client: client.settings(), timeout=20)
        if include_sensitive is True:
            return result
        return self._redact_settings(result)

    def get_settings_schema(self, name: str) -> dict[str, Any]:
        return self._on_device_call(
            name, lambda client: client.settings_schema(), timeout=20
        )

    def update_settings(self, name: str, values: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(values, dict) or not values:
            return {"status": "failed", "reason": "invalid_settings"}
        result = self._on_device_call(
            name, lambda client: client.update_settings(values), timeout=45
        )
        if result["status"] == "failed":
            return result
        applied = result.get("applied")
        rejected = result.get("rejected")
        unknown = result.get("unknown")
        if applied:
            result["status"] = "partial" if rejected or unknown else "pass"
        else:
            result["status"] = "failed"
            result["reason"] = "settings_rejected" if rejected or unknown else "no_settings_applied"
        return result

    def run_action(
        self,
        name: str,
        action: str,
        value: Any = None,
        value2: Any = None,
    ) -> dict[str, Any]:
        if not isinstance(action, str) or not action.strip():
            return {"status": "failed", "reason": "invalid_action"}
        return self._on_device_call(
            name,
            lambda client: client.action(action, value, value2),
            timeout=60,
        )

    def sleep(self, name: str) -> dict[str, Any]:
        return self._on_device_call(name, lambda client: client.sleep())

    def wake(self, name: str) -> dict[str, Any]:
        return self._on_device_call(name, lambda client: client.wake())

    def device_status(self, name: str) -> dict[str, Any]:
        device = self.device(name)
        image = self.image_path(device["name"])
        state = self.state().get("devices", {}).get(device["name"], {})
        status: dict[str, Any] = {
            "name": device["name"],
            "display_name": device.get("display_name"),
            "enabled": bool(device.get("enabled", True)),
            "orientation": normalise_orientation(device.get("orientation")),
            "local_ip": device.get("local_ip"),
            "image": str(image) if image else None,
            "gallery_id": state.get("gallery_id"),
            "reachable": None,
            "last_reachability_check_at": state.get("last_reachability_check_at"),
            "state": state,
            "current_mode": None,
            "modes": [],
            "controls": None,
        }
        if device.get("local_ip"):
            try:
                ident = self._client(device, 3).identify()
                status["reachable"] = True
                status["frame"] = {
                    "alias": ident.get("alias"),
                    "version": ident.get("version"),
                    "orientation": ident.get("orientation"),
                    "name": ident.get("name"),
                }
                controls = ident.get("controls")
                status["controls"] = controls if isinstance(controls, dict) else None
                status["current_mode"] = controls.get("active_mode") if isinstance(controls, dict) else None
                status["modes"] = controls.get("modes", []) if isinstance(controls, dict) else []
                status["current_gallery"] = self._client(device, 3).current_gallery()
            except Exception as exc:
                status["reachable"] = False
                status["error"] = str(exc)
            state = self.state().setdefault("devices", {}).setdefault(device["name"], {})
            state["reachable"] = status["reachable"]
            state["last_reachability_check_at"] = now_iso()
            self.save_state(self.state() | {})
        return status

    def assign_image(self, name: str, path: Path) -> dict[str, Any]:
        return self.set_image(name, Path(path).read_bytes(), Path(path).name)

    def summary_status(self) -> dict[str, Any]:
        return self.summary()

    def summary(self) -> dict[str, Any]:
        return {
            "devices_total": len(self.devices()),
            "devices_enabled": sum(1 for d in self.devices() if d.get("enabled", True)),
        }

    def run_daemon(self, poll_seconds: Optional[int] = None) -> None:
        """Apply sleep schedules; re-publish every N polls if configured."""
        interval = poll_seconds or int(self.config.get("poll_seconds", 60))
        reload_after = int(self.config.get("reload_after_seconds", 0) or 0)
        ticks = 0
        while True:
            try:
                self.apply_sleep_schedules()
                if reload_after and ticks % max(1, reload_after // interval) == 0:
                    for device in self.devices():
                        if device.get("enabled", True) and self.image_path(device["name"]):
                            self.publish(device["name"])
            except Exception as exc:  # keep the loop alive
                state = self.state()
                state["last_daemon_error"] = f"{now_iso()} {exc}"
                self.save_state(state)
            ticks += interval
            time.sleep(interval)

    # ---- sleep schedules -------------------------------------------------
    @staticmethod
    def _valid_hhmm(value: str) -> bool:
        try:
            hour, minute = value.split(":")
            return 0 <= int(hour) <= 23 and 0 <= int(minute) <= 59
        except (ValueError, AttributeError):
            return False

    def get_schedule(self, name: str) -> dict[str, Any]:
        device = self.device(name)
        return device.get("schedule") or {"enabled": False, "timezone": "Etc/UTC",
                                          "sleep_start": "19:30", "wake_time": "07:00"}

    def set_schedule(self, name: str, sleep_start: str, wake_time: str,
                     timezone: str = "Etc/UTC", enabled: bool = True) -> dict[str, Any]:
        device = self.device(name)
        if not self._valid_hhmm(sleep_start) or not self._valid_hhmm(wake_time):
            return {"status": "failed", "reason": "invalid_time", "expected": "HH:MM"}
        if sleep_start == wake_time:
            return {"status": "failed", "reason": "identical_times"}
        try:
            ZoneInfo(timezone)
        except Exception:
            return {"status": "failed", "reason": "unknown_timezone", "timezone": timezone}
        device["schedule"] = {"enabled": bool(enabled), "timezone": timezone,
                              "sleep_start": sleep_start, "wake_time": wake_time}
        save_config(self.config, self.root)
        return {"status": "pass", "schedule": device["schedule"]}

    def clear_schedule(self, name: str) -> dict[str, Any]:
        device = self.device(name)
        device.pop("schedule", None)
        save_config(self.config, self.root)
        return {"status": "pass"}

    def _sleep_schedule_for(self, device: dict) -> Optional[dict[str, Any]]:
        schedule = device.get("schedule")
        if schedule and schedule.get("enabled", True):
            return schedule
        return None

    def apply_sleep_schedules(self) -> list[dict[str, Any]]:
        """Suspend/resume frames at schedule edges. Returns what changed."""
        changed = []
        state = self.state()
        for device in self.devices():
            schedule = self._sleep_schedule_for(device)
            if not schedule:
                continue
            zone = ZoneInfo(schedule.get("timezone", "UTC"))
            local = self.now_provider().astimezone(zone).strftime("%H:%M")
            start, end = schedule.get("sleep_start", "19:30"), schedule.get("wake_time", "07:00")
            in_window = start <= local < end if start < end else (local >= start or local < end)
            entry = state.setdefault("devices", {}).setdefault(device["name"], {})
            if in_window == entry.get("asleep"):
                continue
            try:
                result = self.sleep(device["name"]) if in_window else self.wake(device["name"])
                if result.get("status") != "pass":
                    entry["sleep_error"] = result.get("reason") or result.get("error") or "sleep_action_failed"
                    continue
                entry.pop("sleep_error", None)
                entry["asleep"] = in_window
                changed.append({"device": device["name"], "asleep": in_window})
            except Exception as exc:
                entry["sleep_error"] = str(exc)
        self.save_state(state)
        return changed
