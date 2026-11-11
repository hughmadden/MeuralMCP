import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from meural_mcp.api import create_app
from meural_mcp.manager import ManagerService


def png(width: int, height: int) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (20, 30, 40)).save(buffer, "PNG")
    return buffer.getvalue()


TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.frame = Mock()
        self.frame.gallery_exists.return_value = False
        self.frame.create_gallery.return_value = "5555"
        self.frame.upload_item.return_value = "777"
        self.frame.gallery_items.return_value = [{"id": "777"}]
        self.frame.identify.return_value = {"alias": "Canvas", "orientation": "landscape"}
        self.frame.current_gallery.return_value = {"current_gallery": "5555", "current_item": "777"}
        config = {
            "api_token": TOKEN,
            "devices": [{
                "name": "canvas-1", "display_name": "Canvas 1", "local_ip": "192.0.2.10",
                "orientation": "landscape", "enabled": True, "api_token": "frame-token",
            }],
        }
        service = ManagerService(
            root=self.root,
            config=config,
            local_client_factory=lambda ip, timeout=5: self.frame,
        )
        os.environ["MEURAL_MCP_API_TOKEN"] = TOKEN
        self.patcher = patch("meural_mcp.api.service", return_value=service)
        self.patcher.start()
        self.client = TestClient(create_app())

    def tearDown(self):
        self.patcher.stop()
        os.environ.pop("MEURAL_MCP_API_TOKEN", None)
        self.tmp.cleanup()

    def test_healthz_open(self):
        self.assertEqual(self.client.get("/healthz").status_code, 200)

    def test_agent_docs_served_without_token(self):
        self.assertEqual(self.client.get("/llms.txt").status_code, 200)
        self.assertIn("MCP tools", self.client.get("/llms.txt").text)
        self.assertEqual(self.client.get("/agents.md").status_code, 200)

    def test_openapi_declares_bearer_auth(self):
        schema = self.client.get("/openapi.json").json()
        self.assertIn("HTTPBearer", schema["components"]["securitySchemes"])
        self.assertTrue(schema["paths"]["/devices"]["get"]["security"])

    def test_requires_token(self):
        self.assertEqual(self.client.get("/status").status_code, 401)

    def test_list_devices(self):
        body = self.client.get("/devices", headers=AUTH).json()
        self.assertEqual(len(body["devices"]), 1)

    def test_put_image(self):
        response = self.client.put(
            "/devices/canvas-1/image",
            headers={**AUTH, "Content-Type": "image/png"},
            content=png(1920, 1080),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "pass")
        self.assertEqual(self.client.get("/devices/canvas-1/image", headers=AUTH).status_code, 200)

    def test_put_image_wrong_orientation(self):
        response = self.client.put(
            "/devices/canvas-1/image",
            headers={**AUTH, "Content-Type": "image/png"},
            content=png(1080, 1920),
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["reason"], "orientation_mismatch")

    def test_publish_and_clear(self):
        self.client.put("/devices/canvas-1/image", headers={**AUTH, "Content-Type": "image/png"}, content=png(1920, 1080))
        self.assertEqual(self.client.post("/devices/canvas-1/publish", headers=AUTH).json()["status"], "pass")
        self.assertEqual(self.client.delete("/devices/canvas-1/image", headers=AUTH).json()["status"], "pass")

    def test_schedule_roundtrip(self):
        body = {"sleep_start": "22:30", "wake_time": "07:00", "timezone": "Etc/UTC"}
        self.assertEqual(self.client.put("/devices/canvas-1/schedule", headers=AUTH, json=body).status_code, 200)
        self.assertEqual(self.client.get("/devices/canvas-1/schedule", headers=AUTH).json()["sleep_start"], "22:30")
        self.assertEqual(self.client.delete("/devices/canvas-1/schedule", headers=AUTH).status_code, 200)

    def test_schedule_invalid_time(self):
        response = self.client.put("/devices/canvas-1/schedule", headers=AUTH,
                                   json={"sleep_start": "99:00", "wake_time": "07:00"})
        self.assertEqual(response.status_code, 422)

    def test_unknown_device(self):
        self.assertEqual(self.client.get("/devices/nope", headers=AUTH).status_code, 404)


if __name__ == "__main__":
    unittest.main()
