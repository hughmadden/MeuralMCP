from pathlib import Path
from typing import Any, Optional

import requests

from .video import (copy_video_download, playback_payload, resource_id,
                    upload_options, validate_presentation)
from .modes import mode_id


def verify_tls_from_value(value: Optional[str]) -> bool:
    if value is None:
        return True
    return value.strip().lower() not in {"0", "false", "no", "off"}


class RemoteApiClient:
    def __init__(self, base_url: str, token: str, verify_tls: bool = True):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.verify_tls = verify_tls

    def _headers(self, extra: Optional[dict[str, str]] = None) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.token}"}
        if extra:
            headers.update(extra)
        return headers

    def request(self, method: str, path: str, **kwargs) -> dict[str, Any]:
        response = requests.request(
            method,
            f"{self.base_url}{path}",
            headers=self._headers(kwargs.pop("headers", None)),
            verify=self.verify_tls,
            timeout=kwargs.pop("timeout", 30),
            **kwargs,
        )
        try:
            data = response.json()
        except ValueError:
            data = {"status": "failed", "error": response.text}
        if response.status_code >= 400 and isinstance(data, dict) and "status" not in data:
            data["status"] = "failed"
        if response.status_code >= 400 and isinstance(data, dict):
            data["http_status"] = response.status_code
        return data

    def summary_status(self) -> dict[str, Any]:
        return self.request("GET", "/status", timeout=15)

    def list_devices(self) -> dict[str, Any]:
        return self.request("GET", "/devices", timeout=30)

    def device_status(self, name: str) -> dict[str, Any]:
        return self.request("GET", f"/devices/{name}", timeout=15)

    def get_device_schedule(self, name: str) -> dict[str, Any]:
        return self.request("GET", f"/devices/{name}/schedule", timeout=15)

    def set_device_schedule(self, name: str, sleep_start: str, wake_time: str,
                            timezone: str = "Etc/UTC", enabled: bool = True) -> dict[str, Any]:
        return self.request("PUT", f"/devices/{name}/schedule", timeout=15, json={
            "sleep_start": sleep_start, "wake_time": wake_time,
            "timezone": timezone, "enabled": enabled})

    def clear_device_schedule(self, name: str) -> dict[str, Any]:
        return self.request("DELETE", f"/devices/{name}/schedule", timeout=15)

    def clear_device_image(self, name: str) -> dict[str, Any]:
        return self.request("DELETE", f"/devices/{name}/image", timeout=30)

    def publish_device(self, name: str) -> dict[str, Any]:
        return self.request("POST", f"/devices/{name}/publish", timeout=30)

    def sleep_device(self, name: str) -> dict[str, Any]:
        return self.request("POST", f"/devices/{name}/sleep", timeout=30)

    def wake_device(self, name: str) -> dict[str, Any]:
        return self.request("POST", f"/devices/{name}/wake", timeout=30)

    def set_device_image_bytes(
        self,
        name: str,
        image_bytes: bytes,
        suffix: str = ".png",
        content_type: str | None = None,
    ) -> dict[str, Any]:
        content_type = content_type or ("image/png" if suffix.lower() == ".png" else "image/jpeg")
        return self.request(
            "PUT",
            f"/devices/{name}/image",
            data=image_bytes,
            headers={"Content-Type": content_type},
            timeout=90,
        )

    def device_time(self, name: str) -> dict[str, Any]:
        return self.request("GET", f"/devices/{name}/time", timeout=15)

    def list_galleries(self, name: str) -> dict[str, Any]:
        return self.request("GET", f"/devices/{name}/galleries", timeout=20)

    def get_gallery(self, name: str, gallery_id: str | int) -> dict[str, Any]:
        return self.request("GET", f"/devices/{name}/galleries/{gallery_id}", timeout=20)

    def create_gallery(self, name: str, gallery_name: str) -> dict[str, Any]:
        return self.request("POST", f"/devices/{name}/galleries", json={"name": gallery_name}, timeout=30)

    def upload_gallery_item(
        self,
        name: str,
        gallery_id: str | int,
        image_bytes: bytes,
        suffix: str = ".png",
        title: Optional[str] = None,
    ) -> dict[str, Any]:
        content_type = "image/png" if suffix.lower() == ".png" else "image/jpeg"
        params = {"title": title} if title else None
        return self.request(
            "POST",
            f"/devices/{name}/galleries/{gallery_id}/items",
            params=params,
            data=image_bytes,
            headers={"Content-Type": content_type},
            timeout=90,
        )

    def show_gallery(self, name: str, gallery_id: str | int) -> dict[str, Any]:
        return self.request("POST", f"/devices/{name}/galleries/{gallery_id}/show", timeout=30)

    def show_item(self, name: str, item_id: str | int) -> dict[str, Any]:
        return self.request("POST", f"/devices/{name}/items/{item_id}/show", timeout=30)

    def delete_gallery(self, name: str, gallery_id: str | int) -> dict[str, Any]:
        return self.request("DELETE", f"/devices/{name}/galleries/{gallery_id}", timeout=30)

    def delete_item(self, name: str, item_id: str | int) -> dict[str, Any]:
        return self.request("DELETE", f"/devices/{name}/items/{item_id}", timeout=30)

    def set_picture(self, name: str, path: str, timeout: int = 600) -> dict[str, Any]:
        return self.request(
            "POST",
            f"/devices/{name}/picture",
            json={"path": path, "timeout": timeout},
            timeout=max(60, timeout + 10),
        )

    def get_settings(self, name: str, include_sensitive: bool = False) -> dict[str, Any]:
        return self.request(
            "GET",
            f"/devices/{name}/settings",
            params={"include_sensitive": str(include_sensitive).lower()},
            timeout=20,
        )

    def get_settings_schema(self, name: str) -> dict[str, Any]:
        return self.request("GET", f"/devices/{name}/settings/schema", timeout=20)

    def update_settings(self, name: str, values: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", f"/devices/{name}/settings", json=values, timeout=45)

    def run_action(self, name: str, action: str, value: Any = None, value2: Any = None) -> dict[str, Any]:
        return self.request(
            "POST",
            f"/devices/{name}/action",
            json={"action": action, "value": value, "value2": value2},
            timeout=60,
        )

    # ---- prepared video --------------------------------------------------
    def modes(self, name: str) -> dict[str, Any]:
        return self.request("GET", f"/devices/{name}/modes")

    def register_mode(self, name: str, definition: dict) -> dict[str, Any]:
        return self.request("POST", f"/devices/{name}/modes", json=definition)

    def unregister_mode(self, name: str, identifier: str) -> dict[str, Any]:
        return self.request("DELETE", f"/devices/{name}/modes/{mode_id(identifier)}")

    def select_mode(self, name: str, identifier: str) -> dict[str, Any]:
        return self.request("POST", f"/devices/{name}/modes/{mode_id(identifier)}/select")

    def navigate_menu(self, name: str, direction: str) -> dict[str, Any]:
        return self.request("POST", f"/devices/{name}/menu", json={"direction": direction})

    def mode_events(self, name: str, after: int = 0, epoch: str | None = None) -> dict[str, Any]:
        params = {"after": after}
        if epoch is not None:
            params["epoch"] = epoch
        return self.request("GET", f"/devices/{name}/modes/events", params=params)

    def set_menu_timeout(self, name: str, identifier: str, seconds: int | None,
                         menu_id: str | None = None) -> dict[str, Any]:
        return self.request("POST", f"/devices/{name}/modes/{mode_id(identifier)}/timeout",
                            json={"seconds": seconds, "menu_id": menu_id})

    # ---- prepared video --------------------------------------------------
    def video_status(self, name: str) -> dict[str, Any]:
        return self.request("GET", f"/devices/{name}/video")

    def video_storage(self, name: str) -> dict[str, Any]:
        return self.request("GET", f"/devices/{name}/video/storage")

    def list_video_galleries(self, name: str) -> dict[str, Any]:
        return self.request("GET", f"/devices/{name}/video/galleries")

    def create_video_gallery(self, name: str, gallery_name: str) -> dict[str, Any]:
        return self.request("POST", f"/devices/{name}/video/galleries", json={"name": gallery_name})

    def get_video_gallery(self, name: str, gallery_id: str) -> dict[str, Any]:
        return self.request("GET", f"/devices/{name}/video/galleries/{resource_id(gallery_id)}")

    def delete_video_gallery(self, name: str, gallery_id: str) -> dict[str, Any]:
        return self.request("DELETE", f"/devices/{name}/video/galleries/{resource_id(gallery_id)}")

    def list_video_files(self, name: str) -> dict[str, Any]:
        return self.request("GET", f"/devices/{name}/video/files")

    def get_video_file(self, name: str, file_id: str) -> dict[str, Any]:
        return self.request("GET", f"/devices/{name}/video/files/{resource_id(file_id)}")

    def delete_video_file(self, name: str, file_id: str) -> dict[str, Any]:
        return self.request("DELETE", f"/devices/{name}/video/files/{resource_id(file_id)}")

    def play_video(self, name: str, url: str | None = None, file_id: str | None = None,
                   gallery_id: str | None = None, repeat: int = 1,
                   presentation: str = "auto") -> dict[str, Any]:
        payload = playback_payload(url=url, file_id=file_id, gallery_id=gallery_id,
                                   repeat=repeat, presentation=presentation)
        return self.request("POST", f"/devices/{name}/video", json=payload)

    def stop_video(self, name: str, presentation: str = "all") -> dict[str, Any]:
        validate_presentation(presentation, stop=True)
        return self.request("POST", f"/devices/{name}/video/stop", json={"presentation": presentation})

    def upload_video_file(self, name: str, gallery_id: str, source: Path,
                          filename: str = "video.mp4", kind: str = "video",
                          title: str | None = None, play: bool = False,
                          repeat: int = 1, presentation: str = "auto") -> dict[str, Any]:
        params = upload_options(filename, kind, title, play, repeat, presentation)
        with source.open("rb") as media:
            return self.request("POST", f"/devices/{name}/video/galleries/{resource_id(gallery_id)}/files",
                                data=media, params=params, timeout=300,
                                headers={"Content-Type": "application/octet-stream"})

    def download_video_file(self, name: str, file_id: str, destination: Path,
                            max_bytes: int | None = None) -> dict[str, Any]:
        with requests.get(f"{self.base_url}/devices/{name}/video/files/{resource_id(file_id)}/data",
                          headers=self._headers(), verify=self.verify_tls,
                          timeout=300, stream=True) as response:
            if response.status_code >= 400:
                try:
                    result = response.json()
                except ValueError:
                    result = {"reason": "download_failed"}
                return {**result, "status": "failed", "http_status": response.status_code}
            return {"status": "pass", **copy_video_download(response, destination, max_bytes)}
