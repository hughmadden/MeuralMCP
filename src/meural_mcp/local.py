"""Frame client.

Two transports:
  * token set   -> the frame's on-device API on :8099 (preferred)
  * no token    -> the old unauthenticated /remote API on port 80 (legacy)
"""

import base64
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

DEVICE_PORT = 8099


class FrameLocalError(RuntimeError):
    def __init__(self, status: int, url: str, body: str = ""):
        self.status = status
        super().__init__("%s -> %s %s" % (url, status, body[:200]))


class FrameLocalClient:
    def __init__(self, host: str, token: Optional[str] = None, timeout: int = 5):
        self.host = host
        self.token = token
        self.timeout = timeout

    @property
    def base_url(self) -> str:
        if self.token:
            return f"http://{self.host}:{DEVICE_PORT}"
        return f"http://{self.host}"

    def _params(self, extra: Optional[dict] = None) -> dict:
        params = dict(extra or {})
        if self.token:
            params["token"] = self.token
        return params

    def _check(self, response: requests.Response) -> None:
        if response.status_code >= 400:
            raise FrameLocalError(response.status_code, response.url, response.text)
        return

    def get(self, path: str, timeout: int | None = None, params: Optional[dict] = None) -> Dict[str, Any]:
        response = requests.get(f"{self.base_url}{path}", timeout=timeout or self.timeout,
                                params=self._params(params))
        self._check(response)
        return response.json()

    def post(self, path: str, *, data: Optional[dict] = None, timeout: int | None = None,
             files: Optional[dict] = None) -> Dict[str, Any]:
        response = requests.post(f"{self.base_url}{path}", data=data, files=files,
                                 timeout=timeout or self.timeout,
                                 params=self._params())
        self._check(response)
        return response.json()

    # ---- identity --------------------------------------------------------
    def identify(self) -> dict:
        if not self.token:
            return self.get("/remote/identify").get("response", {})
        status = self.get("/local/status").get("status", {})
        return {
            "alias": status.get("alias"),
            "name": status.get("alias"),
            "orientation": status.get("orientation"),
            "version": status.get("version"),
            "product_key": status.get("product_key"),
        }

    def status(self) -> dict:
        return self.get("/local/status").get("status", {})

    def current_gallery(self) -> dict:
        if not self.token:
            return self.get("/remote/get_gallery_status_json").get("response", {})
        status = self.status()
        return {"current_gallery": status.get("current_gallery"),
                "current_item": status.get("current_item")}

    # ---- galleries -------------------------------------------------------
    def gallery_items(self, gallery_id: str | int) -> List[dict]:
        if not self.token:
            return self.get(f"/remote/get_frame_items_by_gallery_json/{gallery_id}/").get("response", [])
        return self.get(f"/local/gallery/{gallery_id}").get("gallery", {}).get("items", [])

    def gallery_exists(self, gallery_id: str | int) -> bool:
        if not self.token:
            return True
        try:
            self.get(f"/local/gallery/{gallery_id}")
            return True
        except FrameLocalError as exc:
            if exc.status == 404:
                return False
            raise

    def create_gallery(self, name: str) -> str:
        return str(self.post("/local/gallery", data={"name": name}).get("id"))

    def upload_item(self, gallery_id: str | int, image_path: Path,
                    title: Optional[str] = None) -> str:
        mime = "image/png" if image_path.suffix.lower() == ".png" else "image/jpeg"
        with image_path.open("rb") as fh:
            payload = fh.read()
        data = {"title": title} if title else None
        result = self.post(f"/local/item/{gallery_id}", data=data,
                           files={"photo": (image_path.name, payload, mime)}, timeout=60)
        return str(result.get("id"))

    def delete_item(self, item_id: str | int) -> dict:
        return self.post(f"/local/delete/item/{item_id}")

    def delete_gallery(self, gallery_id: str | int) -> dict:
        return self.post(f"/local/delete/gallery/{gallery_id}")

    # ---- actions ---------------------------------------------------------
    def change_gallery(self, gallery_id: str | int) -> dict:
        if not self.token:
            return self.get(f"/remote/control_command/change_gallery/{gallery_id}")
        return self.post(f"/local/show/gallery/{gallery_id}")

    def change_item(self, item_id: str | int) -> dict:
        if not self.token:
            return self.get(f"/remote/control_command/change_item/{item_id}")
        return self.post(f"/local/show/item/{item_id}")

    def sleep(self) -> dict:
        if not self.token:
            return self.get("/remote/control_command/suspend")
        return self.post("/local/action", data={"do": "suspend"})

    def wake(self) -> dict:
        if not self.token:
            return self.get("/remote/control_command/resume")
        return self.post("/local/action", data={"do": "resume"})

    def postcard(self, image_path: Path) -> dict:
        encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
        return self.post("/remote/postcard", data={"photo": encoded}, timeout=30)
