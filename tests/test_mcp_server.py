import base64
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from meural_mcp.mcp_server import (
    mcp_clear_device_image,
    mcp_get_device_image,
    mcp_list_devices,
    mcp_set_device_image_data,
)


def png(width: int, height: int) -> str:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (20, 30, 40)).save(buffer, "PNG")
    return base64.b64encode(buffer.getvalue()).decode()


class McpServerTests(unittest.TestCase):
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
        self.frame_patcher = patch("meural_mcp.manager.FrameLocalClient", return_value=self.frame)
        self.frame_patcher.start()
        self.config = {
            "devices": [{
                "name": "canvas-1", "display_name": "Canvas 1",
                "local_ip": "192.0.2.10", "orientation": "landscape",
                "enabled": True, "api_token": "frame-token",
            }],
        }

    def tearDown(self):
        self.frame_patcher.stop()
        self.tmp.cleanup()

    def test_list_devices(self):
        result = mcp_list_devices(storage_dir=self.root, config=self.config)
        self.assertEqual(len(result["devices"]), 1)
        self.assertEqual(result["devices"][0]["name"], "canvas-1")
        self.assertNotIn("state", result["devices"][0])

    def test_remote_summary_failure_is_preserved(self):
        remote = Mock()
        failure = {"status": "failed", "reason": "unauthorized"}
        remote.summary_status.return_value = failure
        with patch("meural_mcp.mcp_server._remote_client", return_value=remote):
            self.assertEqual(mcp_list_devices(config=self.config), failure)
        remote.list_devices.assert_not_called()

    def test_remote_inventory_failure_is_preserved(self):
        remote = Mock()
        remote.summary_status.return_value = {"devices_total": 1}
        failure = {"status": "failed", "reason": "upstream_unavailable"}
        remote.list_devices.return_value = failure
        with patch("meural_mcp.mcp_server._remote_client", return_value=remote):
            self.assertEqual(mcp_list_devices(config=self.config), failure)

    def test_set_image_data_then_get(self):
        result = mcp_set_device_image_data(
            "canvas-1", png(1920, 1080), "a.png", storage_dir=self.root, config=self.config
        )
        self.assertEqual(result["status"], "pass")
        self.assertTrue(result["gallery_id"])
        self.assertTrue(mcp_get_device_image("canvas-1", storage_dir=self.root, config=self.config)["exists"])

    def test_set_image_data_rejects_wrong_orientation(self):
        result = mcp_set_device_image_data(
            "canvas-1", png(1080, 1920), "a.png", storage_dir=self.root, config=self.config
        )
        self.assertEqual(result["reason"], "orientation_mismatch")

    def test_set_image_data_rejects_bad_base64(self):
        result = mcp_set_device_image_data(
            "canvas-1", "!!!", "a.png", storage_dir=self.root, config=self.config
        )
        self.assertEqual(result["reason"], "image_base64_invalid")

    def test_unknown_device(self):
        result = mcp_get_device_image("nope", storage_dir=self.root, config=self.config)
        self.assertEqual(result["reason"], "device_not_found")

    def test_clear_image(self):
        mcp_set_device_image_data("canvas-1", png(1920, 1080), "a.png", storage_dir=self.root, config=self.config)
        self.assertEqual(mcp_clear_device_image("canvas-1", storage_dir=self.root, config=self.config)["status"], "pass")


if __name__ == "__main__":
    unittest.main()
