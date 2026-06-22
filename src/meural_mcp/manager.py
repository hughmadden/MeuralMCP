import json
import os
import shutil
import struct
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from .cloud import MeuralCloudClient
from .local import FrameLocalClient


DEFAULT_TIMEOUTS = {
    "imageDuration": 86400,
    "previewDuration": 86400,
    "overlayDuration": 120,
}

DEFAULT_BLANK_GALLERIES = {
    "landscape": {
        "name": "MeuralMCP Blank Hold Landscape",
        "description": "Single blank landscape image managed by meural-mcp.",
        "orientation": "landscape",
        "width": 1920,
        "height": 1080,
    },
    "portrait": {
        "name": "MeuralMCP Blank Hold Portrait",
        "description": "Single blank portrait image managed by meural-mcp.",
        "orientation": "portrait",
        "width": 1080,
        "height": 1920,
    },
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
        "reload_after_seconds": 23 * 60 * 60,
        "timeouts": dict(DEFAULT_TIMEOUTS),
        "blank_galleries": json.loads(json.dumps(DEFAULT_BLANK_GALLERIES)),
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
    devices = {}
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
    return json.loads(path.read_text())


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
        while i < len(data):
            while i < len(data) and data[i] == 0xFF:
                i += 1
            if i >= len(data):
                break
            marker = data[i]
            i += 1
            if marker in {0xD8, 0xD9}:
                continue
            if i + 2 > len(data):
                break
            segment_length = struct.unpack(">H", data[i : i + 2])[0]
            if marker in range(0xC0, 0xD0) and marker not in {0xC4, 0xC8, 0xCC}:
                if i + 7 > len(data):
                    break
                height, width = struct.unpack(">HH", data[i + 3 : i + 7])
                return width, height
            i += segment_length
    raise ValueError("unsupported image format")


def orientation_for_dimensions(width: int, height: int) -> str:
    if width == height:
        return "square"
    return "landscape" if width > height else "portrait"


class ManagerService:
    def __init__(
        self,
        root: Optional[Path] = None,
        config: Optional[dict[str, Any]] = None,
        cloud_client: Optional[MeuralCloudClient] = None,
        preview_writer: Optional[Callable[[dict, Path], dict]] = None,
        reachability_checker: Optional[Callable[[dict], bool]] = None,
    ):
        self.root = root or storage_dir()
        self.config = config or load_config(self.root)
        self.cloud_client = cloud_client
        self.preview_writer = preview_writer or self._write_preview_to_frame
        self.reachability_checker = reachability_checker or self._check_reachability
        self.images_dir.mkdir(parents=True, exist_ok=True)

    @property
    def images_dir(self) -> Path:
        return self.root / "images"

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

    def device_status(self, name: str) -> dict[str, Any]:
        device = self.device(name)
        image = self.image_path(device["name"])
        state = self.state().get("devices", {}).get(device["name"], {})
        status = {
            "name": device["name"],
            "display_name": device.get("display_name"),
            "cloud_id": device.get("cloud_id"),
            "enabled": bool(device.get("enabled")),
            "orientation": device.get("orientation", "landscape"),
            "local_ip": device.get("local_ip"),
            "reachable": state.get("reachable"),
            "last_reachability_check_at": state.get("last_reachability_check_at"),
            "image": str(image) if image else None,
            "state": state,
        }
        if self.cloud_client and device.get("cloud_id"):
            try:
                data = self.cloud_client.get_device(int(device["cloud_id"])).get("data", {})
                status["cloud"] = {
                    "status": data.get("status"),
                    "localIp": data.get("localIp"),
                    "orientation": data.get("orientation"),
                    "imageDuration": data.get("imageDuration"),
                    "previewDuration": data.get("previewDuration"),
                    "overlayDuration": data.get("overlayDuration"),
                    "frameStatus": data.get("frameStatus"),
                }
            except Exception as exc:
                status["cloud_error"] = str(exc)
        return status

    def summary_status(self) -> dict[str, Any]:
        devices = [self.device_status(device["name"]) for device in self.devices()]
        reachable = [device for device in devices if device.get("reachable") is True]
        enabled = [device for device in devices if device.get("enabled")]
        return {
            "device_count": len(devices),
            "enabled_count": len(enabled),
            "reachable_count": len(reachable),
            "devices": devices,
        }

    def assign_image(self, name: str, source_path: Path) -> dict[str, Any]:
        device = self.device(name)
        try:
            width, height = image_dimensions(source_path)
        except Exception as exc:
            self._record_failure(device["name"], "image_invalid", str(exc))
            return {"status": "failed", "reason": "image_invalid", "error": str(exc)}
        actual_orientation = orientation_for_dimensions(width, height)
        expected_orientation = device.get("orientation", "landscape")
        if actual_orientation != expected_orientation:
            self._record_failure(device["name"], "orientation_mismatch")
            return {
                "status": "failed",
                "reason": "orientation_mismatch",
                "expected_orientation": expected_orientation,
                "actual_orientation": actual_orientation,
                "width": width,
                "height": height,
            }
        suffix = source_path.suffix.lower()
        if suffix not in {".jpg", ".jpeg", ".png"}:
            suffix = ".jpg"
        with tempfile.NamedTemporaryFile(dir=self.images_dir, suffix=suffix, delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            shutil.copyfile(source_path, tmp_path)
            preview_result = self.preview_writer(device, tmp_path)
            final_path = self.images_dir / f"{device['name']}{suffix}"
            tmp_path.replace(final_path)
            self._record_success(device["name"], final_path, preview_result)
            return {"status": "loaded", "device": device["name"], "image": str(final_path), "preview": preview_result}
        except Exception as exc:
            tmp_path.unlink(missing_ok=True)
            self._record_failure(device["name"], "preview_failed", str(exc))
            return {"status": "failed", "reason": "preview_failed", "error": str(exc)}

    def preview_stored_image(self, name: str) -> dict[str, Any]:
        device = self.device(name)
        image = self.image_path(device["name"])
        if not image:
            return {"status": "failed", "reason": "no_image"}
        try:
            result = self.preview_writer(device, image)
            self._record_success(device["name"], image, result)
            return {"status": "loaded", "device": device["name"], "image": str(image), "preview": result}
        except Exception as exc:
            self._record_failure(device["name"], "preview_failed", str(exc))
            return {"status": "failed", "reason": "preview_failed", "error": str(exc)}

    def poll_once(self) -> dict[str, Any]:
        results = {}
        reload_after = int(self.config.get("reload_after_seconds", 23 * 60 * 60))
        state = self.state()
        for device in self.devices():
            name = device["name"]
            if not device.get("enabled", True):
                results[name] = {"status": "skipped", "reason": "disabled"}
                continue
            image = self.image_path(name)
            if not image:
                results[name] = {"status": "skipped", "reason": "no_image"}
                continue
            if not self._device_reachable(device):
                results[name] = {"status": "skipped", "reason": "unreachable"}
                continue
            last_success = state.get("devices", {}).get(name, {}).get("last_success_at")
            if last_success and not older_than(last_success, reload_after):
                results[name] = {"status": "skipped", "reason": "fresh"}
                continue
            results[name] = self.preview_stored_image(name)
        return results

    def run_daemon(self) -> None:
        poll_seconds = int(self.config.get("poll_seconds", 60))
        while True:
            try:
                self.poll_once()
            except Exception:
                pass
            time.sleep(poll_seconds)

    def _write_preview_to_frame(self, device: dict, image_path: Path) -> dict:
        ip = device.get("local_ip")
        if not ip and self.cloud_client and device.get("cloud_id"):
            data = self.cloud_client.get_device(int(device["cloud_id"])).get("data", {})
            ip = data.get("localIp") or (data.get("frameStatus") or {}).get("localIp")
        if not ip:
            raise TimeoutError("no local IP for device")
        return FrameLocalClient(ip, timeout=5).postcard(image_path)

    def _check_reachability(self, device: dict) -> bool:
        ip = device.get("local_ip")
        if not ip:
            return False
        try:
            FrameLocalClient(ip, timeout=3).identify()
            return True
        except Exception:
            return False

    def _device_reachable(self, device: dict) -> bool:
        name = device["name"]
        try:
            reachable = bool(self.reachability_checker(device))
        except Exception as exc:
            self._record_reachability(name, False, str(exc))
            return False
        self._record_reachability(name, reachable, None if reachable else "unreachable")
        return reachable

    def _record_success(self, name: str, image: Path, preview_result: dict) -> None:
        state = self.state()
        current = state.setdefault("devices", {}).setdefault(name, {})
        current.update({
            "last_success_at": now_iso(),
            "last_error": None,
            "image": str(image),
            "preview": preview_result,
        })
        self.save_state(state)

    def _record_failure(self, name: str, reason: str, error: Optional[str] = None) -> None:
        state = self.state()
        current = state.setdefault("devices", {}).setdefault(name, {})
        current.update({"last_failure_at": now_iso(), "last_error": {"reason": reason, "error": error}})
        self.save_state(state)

    def _record_reachability(self, name: str, reachable: bool, error: Optional[str] = None) -> None:
        state = self.state()
        current = state.setdefault("devices", {}).setdefault(name, {})
        current.update(
            {
                "reachable": reachable,
                "last_reachability_check_at": now_iso(),
            }
        )
        if not reachable:
            current["last_error"] = {"reason": "unreachable", "error": error}
        self.save_state(state)


def older_than(timestamp: str, seconds: int) -> bool:
    try:
        dt = datetime.fromisoformat(timestamp)
    except Exception:
        return True
    return (datetime.now(timezone.utc) - dt).total_seconds() >= seconds


def refresh_device_ids_from_cloud(cloud_client: MeuralCloudClient, config: dict[str, Any]) -> dict:
    response = cloud_client.list_devices()
    devices = response.get("data", [])
    by_alias = {canonical_name(device.get("alias", "")): device for device in devices if device.get("alias")}
    refreshed = []
    for configured in config.get("devices", []):
        cloud_device = by_alias.get(canonical_name(configured.get("display_name", configured["name"])))
        if not cloud_device:
            continue
        configured["cloud_id"] = cloud_device.get("id")
        configured["local_ip"] = cloud_device.get("localIp")
        refreshed.append(configured["name"])
    return {"refreshed": refreshed}


def initialise_cloud_timeouts(cloud_client: MeuralCloudClient, config: dict[str, Any], include_disabled: bool = True) -> dict:
    timeouts = config.get("timeouts", DEFAULT_TIMEOUTS)
    updated, failed = [], []
    for device in config.get("devices", []):
        if not include_disabled and not device.get("enabled", True):
            continue
        cloud_id = device.get("cloud_id")
        if not cloud_id:
            continue
        try:
            cloud_client.update_device(int(cloud_id), dict(timeouts))
            cloud_client.sync_device(int(cloud_id))
            updated.append(device["name"])
        except Exception as exc:
            failed.append({"device": device.get("name"), "error": str(exc)})
    return {"updated": updated, "failed": failed}


def ensure_blank_gallery_assigned(cloud_client: MeuralCloudClient, config: dict[str, Any], root: Path) -> dict:
    blank_galleries = _normalise_blank_gallery_config(config)
    devices_by_orientation: dict[str, list[dict]] = {}
    for device in config.get("devices", []):
        cloud_id = device.get("cloud_id")
        if not cloud_id:
            continue
        orientation = device.get("orientation") or "landscape"
        if orientation not in {"landscape", "portrait"}:
            orientation = "landscape"
        devices_by_orientation.setdefault(orientation, []).append(device)

    results = {}
    for orientation, devices in devices_by_orientation.items():
        gallery_config = blank_galleries[orientation]
        gallery = find_gallery_by_name(cloud_client, gallery_config["name"])
        created = False
        if not gallery:
            gallery = cloud_client.create_gallery(
                gallery_config["name"],
                gallery_config.get("description", ""),
                orientation,
            ).get("data", {})
            created = True
        gallery_id = int(gallery["id"])
        gallery_config["id"] = gallery_id

        blank_image = root / f"blank-black-{orientation}-{gallery_config['width']}x{gallery_config['height']}.png"
        if not blank_image.exists():
            write_blank_png(blank_image, gallery_config["width"], gallery_config["height"])

        uploaded_item_id = None
        if not cloud_client.list_gallery_items(gallery_id).get("data", []):
            uploaded = cloud_client.upload_item_to_gallery(gallery_id, blank_image).get("data", {})
            uploaded_item_id = uploaded.get("id")

        assigned, already_assigned, failed = [], [], []
        for device in devices:
            cloud_id = device.get("cloud_id")
            if not cloud_id:
                continue
            try:
                galleries = cloud_client.list_device_galleries(int(cloud_id)).get("data", [])
                has_gallery = any(int(g.get("id")) == gallery_id for g in galleries if g.get("id") is not None)
                if has_gallery:
                    already_assigned.append(device["name"])
                    continue
                cloud_client.set_device_gallery(int(cloud_id), gallery_id)
                assigned.append(device["name"])
            except Exception as exc:
                failed.append({"device": device.get("name"), "error": str(exc)})
        results[orientation] = {
            "gallery_id": gallery_id,
            "created": created,
            "uploaded_item_id": uploaded_item_id,
            "assigned": assigned,
            "already_assigned": already_assigned,
            "failed": failed,
        }

    config["blank_galleries"] = blank_galleries
    config.pop("blank_gallery", None)
    return {"galleries": results}


def _normalise_blank_gallery_config(config: dict[str, Any]) -> dict[str, dict]:
    blank_galleries = json.loads(json.dumps(DEFAULT_BLANK_GALLERIES))
    if "blank_galleries" in config:
        for orientation, value in config["blank_galleries"].items():
            if orientation in blank_galleries and isinstance(value, dict):
                blank_galleries[orientation].update(value)
    elif "blank_gallery" in config:
        legacy = config["blank_gallery"]
        if isinstance(legacy, dict):
            blank_galleries["landscape"].update(legacy)
            blank_galleries["landscape"].setdefault("width", 1920)
            blank_galleries["landscape"].setdefault("height", 1080)
    return blank_galleries


def find_gallery_by_name(cloud_client: MeuralCloudClient, name: str) -> Optional[dict]:
    for gallery in cloud_client.list_galleries().get("data", []):
        if gallery.get("name") == name:
            return gallery
    return None


def write_blank_png(path: Path, width: int = 1920, height: int = 1080) -> None:
    import zlib

    path.parent.mkdir(parents=True, exist_ok=True)
    raw = b"".join(b"\x00" + b"\x00\x00\x00" * width for _ in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(kind + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9))
    png += chunk(b"IEND", b"")
    path.write_bytes(png)
