import os
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from meural_mcp.api import create_app

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class FeatureApiTests(unittest.TestCase):
    def setUp(self):
        self.service = Mock()
        self.service.device_time.return_value = {"status": "pass", "time": 123}
        self.service.get_settings.return_value = {"status": "pass", "settings": {}}
        self.service.get_settings_schema.return_value = {"status": "pass", "columns": {}}
        for name in (
            "list_galleries", "get_gallery", "create_gallery", "upload_gallery_item",
            "show_gallery", "show_item", "delete_gallery", "delete_item",
            "set_picture", "update_settings", "run_action",
        ):
            getattr(self.service, name).return_value = {"status": "pass", "result": "ok"}
        os.environ["MEURAL_MCP_API_TOKEN"] = TOKEN
        self.patcher = patch("meural_mcp.api.service", return_value=self.service)
        self.patcher.start()
        self.client = TestClient(create_app())

    def tearDown(self):
        self.patcher.stop()
        os.environ.pop("MEURAL_MCP_API_TOKEN", None)

    def test_time_settings_and_schema(self):
        self.assertEqual(self.client.get("/devices/canvas-1/time", headers=AUTH).json()["time"], 123)
        self.client.get("/devices/canvas-1/settings?include_sensitive=true", headers=AUTH)
        self.service.get_settings.assert_called_with("canvas-1", True)
        self.client.get("/devices/canvas-1/settings/schema", headers=AUTH)
        self.service.get_settings_schema.assert_called_with("canvas-1")

    def test_gallery_routes(self):
        self.client.get("/devices/canvas-1/galleries", headers=AUTH)
        self.service.list_galleries.assert_called_with("canvas-1")
        self.client.get("/devices/canvas-1/galleries/55", headers=AUTH)
        self.service.get_gallery.assert_called_with("canvas-1", "55")
        self.client.post("/devices/canvas-1/galleries", headers=AUTH, json={"name": "New Gallery"})
        self.service.create_gallery.assert_called_with("canvas-1", "New Gallery")
        self.client.post(
            "/devices/canvas-1/galleries/55/items?title=Poster",
            headers={**AUTH, "Content-Type": "image/png"},
            content=b"image-bytes",
        )
        self.service.upload_gallery_item.assert_called_with("canvas-1", "55", b"image-bytes", ".png", "Poster")
        self.client.post("/devices/canvas-1/galleries/55/show", headers=AUTH)
        self.service.show_gallery.assert_called_with("canvas-1", "55")
        self.client.post("/devices/canvas-1/items/66/show", headers=AUTH)
        self.service.show_item.assert_called_with("canvas-1", "66")
        self.client.delete("/devices/canvas-1/galleries/55", headers=AUTH)
        self.service.delete_gallery.assert_called_with("canvas-1", "55")
        self.client.delete("/devices/canvas-1/items/66", headers=AUTH)
        self.service.delete_item.assert_called_with("canvas-1", "66")

    def test_action_picture_and_settings_write(self):
        self.client.post("/devices/canvas-1/picture", headers=AUTH, json={"path": "/tmp/a.jpg", "timeout": 0})
        self.service.set_picture.assert_called_with("canvas-1", "/tmp/a.jpg", 0)
        self.client.post("/devices/canvas-1/settings", headers=AUTH, json={"brightness": "40"})
        self.service.update_settings.assert_called_with("canvas-1", {"brightness": "40"})
        self.client.post("/devices/canvas-1/action", headers=AUTH, json={"action": "resume"})
        self.service.run_action.assert_called_with("canvas-1", "resume", None, None)

    def test_feature_failure_is_422(self):
        self.service.device_time.return_value = {"status": "failed", "reason": "on_device_api_required"}
        response = self.client.get("/devices/canvas-1/time", headers=AUTH)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["reason"], "on_device_api_required")


if __name__ == "__main__":
    unittest.main()
