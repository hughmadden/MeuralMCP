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

    def set_device_image(self, name: str, image_path: Path) -> dict[str, Any]:
        content_type = "image/png" if image_path.suffix.lower() == ".png" else "image/jpeg"
        return self.set_device_image_bytes(name, image_path.read_bytes(), suffix=image_path.suffix, content_type=content_type)

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
