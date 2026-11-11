from pathlib import Path
from typing import Any, Optional

import requests


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
