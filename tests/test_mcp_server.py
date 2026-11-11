import base64
import io
import tempfile
import unittest
from pathlib import Path

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
        for sub in ("media", "by-id", "queues"):
            (self.root / "svc" / sub).mkdir(parents=True)
        self.config = {
            "service": {"root": str(self.root / "svc"), "port": 443},
            "devices": [{
                "name": "canvas-1", "display_name": "Canvas 1",
                "local_ip": "192.0.2.10", "product_key": "KEY-1",
                "orientation": "landscape", "enabled": True,
            }],
        }

    def tearDown(self):
        self.tmp.cleanup()

    def test_list_devices(self):
        result = mcp_list_devices(storage_dir=self.root, config=self.config)
        self.assertEqual(result["devices_total"], 1)
        self.assertEqual(len(result["devices"]), 1)

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
