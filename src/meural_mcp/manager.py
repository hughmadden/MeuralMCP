"""Device registry, image store, and gallery publishing into the local API.

A frame trusts this host for api.meural.com, so "showing an image" means
publishing a one-item gallery into the service's data directory and queueing a
`get_galleries` command for that frame. Nothing here talks to Netgear.
"""

from __future__ import annotations

import hashlib
import json
import os
import struct
import time
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

from .local import FrameLocalClient

GALLERY_BASE = 900000
DEFAULT_SERVICE = {
    "root": "/var/lib/meural-api",
    "cert": "/etc/meural-api/chain.pem",
    "key": "/etc/meural-api/key.pem",
    "bind": "0.0.0.0",
    "port": 443,
}


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
        "service": dict(DEFAULT_SERVICE),
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
    config.update({key: value for key, value in loaded.items() if key != "devices"})
    config["service"] = {**DEFAULT_SERVICE, **(loaded.get("service") or {})}
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


def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def _thumbnail(source: Path, target: Path, orientation: str) -> None:
    from PIL import Image  # imported lazily so the rest works without Pillow

    size = (270, 480) if orientation == "portrait" else (480, 270)
    with Image.open(source) as img:
        img.convert("RGB").resize(size).save(target, "JPEG", quality=85)


class ManagerService:
    def __init__(
        self,
        root: Optional[Path] = None,
        config: Optional[dict[str, Any]] = None,
        local_client_factory: Optional[Callable[[str, int], FrameLocalClient]] = None,
        thumbnailer: Optional[Callable[[Path, Path, str], None]] = None,
        now_provider: Optional[Callable[[], datetime]] = None,
    ):
        self.root = root or storage_dir()
        self.config = config or load_config(self.root)
        self.local_client_factory = local_client_factory or (lambda ip, timeout=5: FrameLocalClient(ip, timeout=timeout))
        self.thumbnailer = thumbnailer or _thumbnail
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self.images_dir.mkdir(parents=True, exist_ok=True)

    # ---- paths -----------------------------------------------------------
    @property
    def images_dir(self) -> Path:
        return self.root / "images"

    @property
    def service(self) -> dict[str, Any]:
        return self.config.get("service") or dict(DEFAULT_SERVICE)

    @property
    def service_root(self) -> Path:
        return Path(self.service["root"])

    @property
    def media_dir(self) -> Path:
        return self.service_root / "media"

    @property
    def by_id_dir(self) -> Path:
        return self.service_root / "by-id"

    @property
    def queues_dir(self) -> Path:
        return self.service_root / "queues"

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

    def gallery_id(self, name: str) -> int:
        return GALLERY_BASE + zlib.crc32(canonical_name(name).encode()) % 90000

    def product_key(self, device: dict) -> Optional[str]:
        if device.get("product_key"):
            return device["product_key"]
        ip = device.get("local_ip")
        if not ip:
            return None
        try:
            ident = self.local_client_factory(ip, 3).identify()
        except Exception:
            return None
        key = ident.get("product_key")
        if key:
            device["product_key"] = key
            save_config(self.config, self.root)
        return key

    def state(self) -> dict[str, Any]:
        return load_state(self.root)

    def save_state(self, state: dict[str, Any]) -> None:
        save_state(self.root, state)

    def service_reachable(self) -> bool:
        host = "127.0.0.1" if self.service.get("bind") in ("0.0.0.0", "::") else self.service.get("bind")
        port = int(self.service.get("port", 443))
        import socket

        try:
            with socket.create_connection((host, port), timeout=3):
                return True
        except OSError:
            return False

    # ---- publishing ------------------------------------------------------
    def _publish_to_device(self, device: dict, image: Path) -> dict[str, Any]:
        """One-item gallery on the device, via its API. Nothing to queue."""
        client = self._client(device, 60)
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
        """Put the device's stored image on the frame.

        With an api_token this goes through the frame's own API: one gallery per
        device, the image uploaded into it, previous items dropped, then shown.
        Without a token the legacy service/queue path is used.
        """
        device = self.device(name)
        image = self.image_path(name)
        if image is None:
            return {"status": "skipped", "reason": "no_image"}
        if device.get("api_token"):
            return self._publish_to_device(device, image)
        orientation = normalise_orientation(device.get("orientation"))
        gid = self.gallery_id(device["name"])
        stem = device["name"]

        self.media_dir.mkdir(parents=True, exist_ok=True)
        self.by_id_dir.mkdir(parents=True, exist_ok=True)

        ext = image.suffix.lower() if image.suffix.lower() in (".jpg", ".jpeg", ".png") else ".jpg"
        full = self.media_dir / f"{stem}{ext}"
        thumb = self.media_dir / f"{stem}_t.jpg"
        full.write_bytes(image.read_bytes())
        self.thumbnailer(full, thumb, orientation)

        host = self.service.get("host") or "api.meural.com"
        item = {
            "id": gid + 1,
            "title": device.get("display_name") or device["name"],
            "is_detailed": False,
            "description": "",
            "medium": "",
            "dimensions": "",
            "location": "",
            "year": "",
            "author": "",
            "creator_id": 1,
            "partner_name": "",
            "brightness": 0,
            "gamma": 0.0,
            "priority": 0,
            "fill": 0,
            "type": "image",
            "link": False,
            "copyright": "",
            "orientation": orientation,
            "url": f"https://{host}/media/{full.name}",
            "thumb_url": f"https://{host}/media/{thumb.name}",
            "permalink": None,
            "bg_type": None,
            "bg_color": None,
            "placement_orientation": None,
            "placement_coord_x": None,
            "placement_coord_y": None,
            "scale": None,
            "overlay_name_font": None,
            "overlay_caption_font": None,
            "is_authenticated": False,
            "thumb_checksum": _md5(thumb),
            "thumb_size": thumb.stat().st_size,
            "checksum": _md5(full),
            "size": full.stat().st_size,
        }
        gallery = {
            "is_ok": True,
            "id": gid,
            "name": f"MeuralMCP {device['name']}",
            "description": "Managed by meural-mcp.",
            "curator": "meural-mcp",
            "type": "images",
            "priority": 0,
            "timeout": 0,
            "orientation": 0 if orientation == "portrait" else 1,
            "items": [item],
            "cover_url": item["thumb_url"],
            "cover_checksum": item["thumb_checksum"],
            "cover_size": item["thumb_size"],
        }
        (self.by_id_dir / f"{gid}.json").write_text(json.dumps(gallery))
        queued = self.enqueue(device, {
            "type": "command",
            "command": "get_galleries",
            "parameter": gid,
            "body": {"id": gid, "include_items": True, "bg_download": False},
        })
        return {"status": "pass", "gallery_id": gid, "queued": queued}

    def enqueue(self, device: dict, command: dict) -> bool:
        key = self.product_key(device)
        if not key:
            return False
        self.queues_dir.mkdir(parents=True, exist_ok=True)
        path = self.queues_dir / f"{key}.json"
        try:
            items = json.loads(path.read_text()) if path.exists() else []
        except ValueError:
            items = []
        items.append(command)
        path.write_text(json.dumps(items))
        return True

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

    def sleep(self, name: str) -> dict[str, Any]:
        device = self.device(name)
        self._client(device).sleep()
        return {"status": "pass"}

    def wake(self, name: str) -> dict[str, Any]:
        device = self.device(name)
        self._client(device).wake()
        return {"status": "pass"}

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
            "product_key": device.get("product_key"),
            "image": str(image) if image else None,
            "gallery_id": state.get("gallery_id") if device.get("api_token") else self.gallery_id(device["name"]),
            "reachable": None,
            "last_reachability_check_at": state.get("last_reachability_check_at"),
            "state": state,
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
            "service": {"reachable": self.service_reachable(), **self.service},
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
                self.sleep(device["name"]) if in_window else self.wake(device["name"])
                entry["asleep"] = in_window
                changed.append({"device": device["name"], "asleep": in_window})
            except Exception as exc:
                entry["sleep_error"] = str(exc)
        self.save_state(state)
        return changed
