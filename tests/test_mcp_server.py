import tempfile
import unittest
import base64
from pathlib import Path
from unittest.mock import Mock, patch

from meural_mcp.mcp_server import mcp_get_device_status, mcp_list_devices, mcp_set_device_image, mcp_set_device_image_data


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
            config = {
                "devices": [
                    {
                        "name": "canvas-1",
                        "display_name": "Living Room",
                        "cloud_id": 1001,
                        "local_ip": "192.0.2.10",
                        "orientation": "landscape",
                        "enabled": True,
                    }
                ]
            }

            result = mcp_list_devices(storage_dir=tmp, config=config)

            self.assertEqual(result["device_count"], 1)
            device = result["devices"][0]
            self.assertEqual(device["name"], "canvas-1")
            self.assertEqual(device["display_name"], "Living Room")
            self.assertEqual(device["cloud_id"], 1001)
            self.assertEqual(device["local_ip"], "192.0.2.10")
            self.assertEqual(device["orientation"], "landscape")
            self.assertTrue(device["enabled"])

    def test_get_device_status_returns_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = {
                "devices": [
                    {
                        "name": "canvas-1",
                        "display_name": "Living Room",
                        "cloud_id": 1001,
                        "local_ip": "192.0.2.10",
                        "orientation": "landscape",
                        "enabled": True,
                    }
                ]
            }

            result = mcp_get_device_status("canvas-1", storage_dir=tmp, config=config)

            self.assertEqual(result["name"], "canvas-1")
            self.assertEqual(result["display_name"], "Living Room")
            self.assertEqual(result["cloud_id"], 1001)
            self.assertEqual(result["local_ip"], "192.0.2.10")
            self.assertEqual(result["orientation"], "landscape")
            self.assertTrue(result["enabled"])

    def test_set_device_image_returns_error_when_thumbnail_or_parse_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "not-image.bin"
            bad.write_bytes(b"not an image")
            config = {"devices": [{"name": "canvas-1", "orientation": "landscape", "enabled": True}]}

            result = mcp_set_device_image("canvas-1", str(bad), storage_dir=tmp, config=config)

            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["reason"], "image_invalid")

    def test_set_device_image_data_accepts_base64_upload(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "landscape.png"
            write_png(image, 1200, 800)
            config = {"devices": [{"name": "canvas-1", "orientation": "landscape", "enabled": True}]}

            result = mcp_set_device_image_data(
                "canvas-1",
                base64.b64encode(image.read_bytes()).decode("ascii"),
                filename="client-image.png",
                storage_dir=tmp,
                config=config,
                preview_writer=Mock(return_value={"status": "pass"}),
            )

            self.assertEqual(result["status"], "loaded")
            self.assertEqual(result["device"], "canvas-1")
            self.assertTrue((Path(tmp) / "images" / "canvas-1.png").exists())

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

    def test_set_device_image_remote_api_uploads_local_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "landscape.png"
            write_png(image, 1200, 800)
            response = Mock()
            response.status_code = 200
            response.json.return_value = {"status": "loaded", "device": "canvas-remote"}

            with patch("meural_mcp.api_client.requests.request", return_value=response) as request:
                result = mcp_set_device_image(
                    "canvas-remote",
                    str(image),
                    api_url="https://meural-mcp.example.test",
                    api_token="token",
                    verify_tls=False,
                )

            self.assertEqual(result, {"status": "loaded", "device": "canvas-remote"})
            method, url = request.call_args.args
            kwargs = request.call_args.kwargs
            self.assertEqual(method, "PUT")
            self.assertEqual(url, "https://meural-mcp.example.test/devices/canvas-remote/image")
            self.assertEqual(kwargs["headers"]["Authorization"], "Bearer token")
            self.assertEqual(kwargs["headers"]["Content-Type"], "image/png")
            self.assertEqual(kwargs["data"], image.read_bytes())
            self.assertFalse(kwargs["verify"])


if __name__ == "__main__":
    unittest.main()
