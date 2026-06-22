import time
from pathlib import Path
from typing import Any, Optional

import requests


# Public Meural service endpoints and app client identifier.
# These are shared by Meural clients; user-specific auth still requires the
# caller's Meural username/password and no credentials are embedded here.
BASE_URL = "https://api.meural.com/v0"
BASE_URL_V1 = "https://api.meural.com/v1"
COGNITO_URL = "https://cognito-idp.eu-west-1.amazonaws.com/"
COGNITO_CLIENT_ID = "487bd4kvb1fnop6mbgk8gu5ibf"


class MeuralCloudClient:
    def __init__(self, username: str, password: str):
        self.username = username
        self.password = password
        self._token: Optional[str] = None

    def token(self) -> str:
        if self._token:
            return self._token
        response = requests.post(
            COGNITO_URL,
            json={
                "AuthFlow": "USER_PASSWORD_AUTH",
                "ClientId": COGNITO_CLIENT_ID,
                "AuthParameters": {"USERNAME": self.username, "PASSWORD": self.password},
            },
            headers={
                "Content-Type": "application/x-amz-json-1.1",
                "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth",
            },
            timeout=20,
        )
        response.raise_for_status()
        self._token = response.json()["AuthenticationResult"]["AccessToken"]
        return self._token

    def headers(self) -> dict:
        return {
            "Authorization": f"Token {self.token()}",
            "x-meural-api-version": "4",
            "x-meural-source-platform": "meural-mcp",
        }

    def request(self, method: str, url: str, *, attempts: int = 3, **kwargs) -> requests.Response:
        last_exc: Exception | None = None
        for attempt in range(attempts):
            try:
                response = requests.request(method, url, headers=self.headers(), **kwargs)
                if response.status_code == 401 and attempt == 0:
                    self._token = None
                    response = requests.request(method, url, headers=self.headers(), **kwargs)
                if response.status_code not in {429, 500, 502, 503, 504}:
                    return response
                last_exc = RuntimeError(f"HTTP {response.status_code}: {response.text[:200]}")
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_exc = exc
            time.sleep(1.5**attempt)
        if last_exc:
            raise last_exc
        raise RuntimeError("request failed without response")

    def list_devices(self) -> dict:
        response = self.request("GET", f"{BASE_URL}/user/devices?count=1000", timeout=20)
        response.raise_for_status()
        return response.json()

    def get_device(self, cloud_id: int) -> dict:
        response = self.request("GET", f"{BASE_URL}/devices/{cloud_id}", timeout=20)
        response.raise_for_status()
        return response.json()

    def update_device(self, cloud_id: int, data: dict) -> dict:
        response = self.request("PUT", f"{BASE_URL}/devices/{cloud_id}", json=data, timeout=20)
        response.raise_for_status()
        return response.json()

    def sync_device(self, cloud_id: int) -> dict:
        response = self.request("POST", f"{BASE_URL}/devices/{cloud_id}/sync", timeout=30)
        response.raise_for_status()
        return response.json()

    def set_device_gallery(self, cloud_id: int, gallery_id: int) -> dict:
        response = self.request("POST", f"{BASE_URL}/devices/{cloud_id}/galleries/{gallery_id}", timeout=45)
        response.raise_for_status()
        return response.json()

    def list_device_galleries(self, cloud_id: int) -> dict:
        response = self.request("GET", f"{BASE_URL}/devices/{cloud_id}/galleries?count=1000", timeout=25)
        response.raise_for_status()
        return response.json()

    def list_galleries(self) -> dict:
        response = self.request("GET", f"{BASE_URL}/user/galleries?count=1000", timeout=25)
        response.raise_for_status()
        return response.json()

    def create_gallery(self, name: str, description: str, orientation: str) -> dict:
        response = self.request(
            "POST",
            f"{BASE_URL_V1}/galleries",
            data={"name": name, "description": description, "orientation": orientation},
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def list_gallery_items(self, gallery_id: int) -> dict:
        response = self.request("GET", f"{BASE_URL_V1}/galleries/{gallery_id}/items?count=100", timeout=25)
        response.raise_for_status()
        return response.json()

    def upload_item_to_gallery(self, gallery_id: int, image_path: Path) -> dict:
        with image_path.open("rb") as image:
            response = self.request(
                "POST",
                f"{BASE_URL_V1}/galleries/{gallery_id}/items",
                files={"image": (image_path.name, image, "image/png")},
                timeout=60,
            )
        response.raise_for_status()
        return response.json()
