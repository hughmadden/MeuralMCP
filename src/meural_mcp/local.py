"""Client for a frame's authenticated on-device API on port 8099."""

from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from .video import copy_video_download, resource_id
from .modes import mode_definition, mode_id

DEVICE_PORT = 8099


class FrameLocalError(RuntimeError):
    def __init__(self, status: int, url: str, body: str = "", secret: Optional[str] = None):
        self.status = status
        if secret:
            url = url.replace(secret, "<redacted>")
            body = body.replace(secret, "<redacted>")
        self.body = body
        super().__init__("%s -> %s %s" % (url, status, body[:200]))


class FrameLocalClient:
    def __init__(self, host: str, token: Optional[str] = None, timeout: int = 5):
        self.host = host
        self.token = token
        self.timeout = timeout

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{DEVICE_PORT}"

    def _params(self, extra: Optional[dict] = None) -> dict:
        params = dict(extra or {})
        if self.token:
            params["token"] = self.token
        return params

    def _check(self, response: requests.Response) -> None:
        if response.status_code >= 400:
            raise FrameLocalError(response.status_code, response.url, response.text, secret=self.token)
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
        payload = self.get("/local/status")
        status = payload.get("status", {})
        return {
            "alias": status.get("alias"),
            "name": status.get("alias"),
            "orientation": status.get("orientation"),
            "version": status.get("version"),
            "controls": payload.get("controls"),
        }

    def status(self) -> dict:
        return self.get("/local/status").get("status", {})

    def time(self) -> dict:
        return self.get("/api/v3/time")

    def current_gallery(self) -> dict:
        status = self.status()
        return {"current_gallery": status.get("current_gallery"),
                "current_item": status.get("current_item")}

    # ---- galleries -------------------------------------------------------
    def list_galleries(self) -> dict:
        return self.get("/local/list")

    def gallery(self, gallery_id: str | int) -> dict:
        return self.get(f"/local/gallery/{gallery_id}")

    def gallery_items(self, gallery_id: str | int) -> List[dict]:
        return self.get(f"/local/gallery/{gallery_id}").get("gallery", {}).get("items", [])

    def gallery_exists(self, gallery_id: str | int) -> bool:
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

    # ---- settings --------------------------------------------------------
    def settings(self) -> dict:
        return self.get("/local/settings")

    def settings_schema(self) -> dict:
        return self.get("/local/settings/schema")

    def update_settings(self, values: dict) -> dict:
        return self.post("/local/settings", data=values)

    # ---- actions ---------------------------------------------------------
    def action(self, action: str, value: Any = None, value2: Any = None) -> dict:
        data = {"do": action}
        if value is not None:
            data["value"] = value
        if value2 is not None:
            data["value2"] = value2
        return self.post("/local/action", data=data)

    def set_picture(self, path: str, timeout: int = 600) -> dict:
        return self.post("/local/setpic", data={"path": path, "timeout": timeout})

    def change_gallery(self, gallery_id: str | int) -> dict:
        return self.post(f"/local/show/gallery/{gallery_id}")

    def change_item(self, item_id: str | int) -> dict:
        return self.post(f"/local/show/item/{item_id}")

    def sleep(self) -> dict:
        return self.post("/local/action", data={"do": "suspend"})

    def wake(self) -> dict:
        return self.post("/local/action", data={"do": "resume"})

    # ---- modes and menu input --------------------------------------------
    def modes(self) -> dict:
        return self.get("/local/modes")

    def register_mode(self, definition: dict) -> dict:
        response = requests.post(f"{self.base_url}/local/modes", json=mode_definition(definition),
                                 timeout=self.timeout, params=self._params())
        self._check(response)
        return response.json()

    def unregister_mode(self, identifier: str) -> dict:
        return self._delete(f"/local/modes/{mode_id(identifier)}")

    def select_mode(self, identifier: str) -> dict:
        return self.post(f"/local/modes/{mode_id(identifier)}/select")

    def navigate_menu(self, direction: str) -> dict:
        return self.action("navigate", direction)

    def mode_events(self, after: int = 0, epoch: str | None = None) -> dict:
        params = {"after": after}
        if epoch is not None:
            params["epoch"] = epoch
        return self.get("/local/modes/events", params=params)

    def set_menu_timeout(self, identifier: str, seconds: int | None, menu_id: str | None = None) -> dict:
        data = {"seconds": "inherit" if seconds is None else seconds}
        if menu_id is not None:
            data["menu_id"] = menu_id
        return self.post(f"/local/modes/{mode_id(identifier)}/timeout", data=data)

    # ---- prepared video --------------------------------------------------
    def video_status(self) -> dict:
        return self.get("/local/video")

    def video_storage(self) -> dict:
        return self.get("/local/video/storage")

    def list_video_galleries(self) -> dict:
        return self.get("/local/video/galleries")

    def create_video_gallery(self, gallery_name: str) -> dict:
        return self.post("/local/video/galleries", data={"name": gallery_name})

    def get_video_gallery(self, gallery_id: str) -> dict:
        return self.get(f"/local/video/galleries/{resource_id(gallery_id)}")

    def delete_video_gallery(self, gallery_id: str) -> dict:
        return self._delete(f"/local/video/galleries/{resource_id(gallery_id)}")

    def list_video_files(self) -> dict:
        return self.get("/local/video/files")

    def get_video_file(self, file_id: str) -> dict:
        return self.get(f"/local/video/files/{resource_id(file_id)}")

    def delete_video_file(self, file_id: str) -> dict:
        return self._delete(f"/local/video/files/{resource_id(file_id)}")

    def _delete(self, path: str) -> dict:
        response = requests.delete(f"{self.base_url}{path}", timeout=self.timeout,
                                   params=self._params())
        self._check(response)
        return response.json()

    def upload_video_file(self, gallery_id: str, source: Path, **options) -> dict:
        # A file handle lets requests stream rather than copy the entire upload.
        params = {key: str(value).lower() if type(value) is bool else value
                  for key, value in options.items() if value is not None}
        with source.open("rb") as media:
            response = requests.post(
                f"{self.base_url}/local/video/galleries/{resource_id(gallery_id)}/files",
                data=media, params=self._params(params),
                headers={"Content-Type": "application/octet-stream"}, timeout=300)
        self._check(response)
        return response.json()

    def download_video_file(self, file_id: str, destination: Path,
                            max_bytes: int | None = None) -> dict:
        with requests.get(f"{self.base_url}/local/video/files/{resource_id(file_id)}/data",
                          params=self._params(), timeout=300, stream=True) as response:
            self._check(response)
            return copy_video_download(response, destination, max_bytes)

    def play_video(self, **payload) -> dict:
        return self.post("/local/video", data=payload, timeout=30)

    def stop_video(self, presentation: str = "all") -> dict:
        return self.post("/local/video/stop", data={"presentation": presentation}, timeout=30)
