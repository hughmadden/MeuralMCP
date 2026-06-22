import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from meural_mcp.mcp_server import mcp_get_device_status, mcp_list_devices, mcp_set_device_image


def write_png(path: Path, width: int, height: int) -> None:
    import struct
    import zlib

    raw = b"".join(b"\x00" + b"\x00\x00\x00" * width for _ in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9))
    png += chunk(b"IEND", b"")
    path.write_bytes(png)


class McpToolTests(unittest.TestCase):
    def test_list_devices_returns_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = {"devices": [{"name": "canvas-1", "orientation": "landscape", "enabled": True}]}

            result = mcp_list_devices(storage_dir=tmp, config=config)

            self.assertEqual(result["device_count"], 1)
            self.assertEqual(result["devices"][0]["name"], "canvas-1")

    def test_get_device_status_returns_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = {"devices": [{"name": "canvas-1", "orientation": "landscape", "enabled": True}]}

            result = mcp_get_device_status("canvas-1", storage_dir=tmp, config=config)

            self.assertEqual(result["name"], "canvas-1")
            self.assertEqual(result["orientation"], "landscape")

    def test_set_device_image_returns_error_when_thumbnail_or_parse_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "not-image.bin"
            bad.write_bytes(b"not an image")
            config = {"devices": [{"name": "canvas-1", "orientation": "landscape", "enabled": True}]}

            result = mcp_set_device_image("canvas-1", str(bad), storage_dir=tmp, config=config)

            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["reason"], "image_invalid")

    def test_set_device_image_returns_error_when_device_load_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "landscape.png"
            write_png(image, 1200, 800)
            config = {"devices": [{"name": "canvas-1", "orientation": "landscape", "enabled": True}]}

            result = mcp_set_device_image(
                "canvas-1",
                str(image),
                storage_dir=tmp,
                config=config,
                preview_writer=Mock(side_effect=TimeoutError("timeout")),
            )

            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["reason"], "preview_failed")


if __name__ == "__main__":
    unittest.main()
