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
