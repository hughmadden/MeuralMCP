import base64
from pathlib import Path
from typing import Any, Dict

import requests


class FrameLocalClient:
    def __init__(self, frame_ip: str, timeout: int = 5):
        self.base_url = f"http://{frame_ip}"
        self.timeout = timeout

    def get(self, path: str, timeout: int | None = None) -> Dict[str, Any]:
        response = requests.get(f"{self.base_url}{path}", timeout=timeout or self.timeout)
        response.raise_for_status()
        return response.json()

    def post(self, path: str, *, data: dict, timeout: int | None = None) -> Dict[str, Any]:
        response = requests.post(f"{self.base_url}{path}", data=data, timeout=timeout or self.timeout)
        response.raise_for_status()
        return response.json()

    def identify(self) -> dict:
        return self.get("/remote/identify").get("response", {})

    def current_gallery(self) -> dict:
        return self.get("/remote/get_gallery_status_json").get("response", {})

    def postcard(self, image_path: Path) -> dict:
        encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
        return self.post("/remote/postcard", data={"photo": encoded}, timeout=30)


