import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from meural_mcp.manager import (
    ManagerService,
    auth_token_valid,
    ensure_blank_gallery_assigned,
    initialise_cloud_timeouts,
    load_config,
)


SINGLE_DEVICE_CONFIG = {
    "devices": [
        {
            "name": "canvas-1",
            "display_name": "Canvas 1",
            "cloud_id": None,
            "local_ip": None,
            "orientation": "landscape",
            "enabled": True,
        }
    ]
}


def write_png(path: Path, width: int, height: int, color: bytes = b"\x00\x00\x00") -> None:
    import struct
    import zlib

    raw = b"".join(b"\x00" + color * width for _ in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(kind + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9))
    png += chunk(b"IEND", b"")
    path.write_bytes(png)


class ConfigTests(unittest.TestCase):
    def test_default_config_has_no_hard_coded_devices(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = load_config(Path(tmp))

            self.assertEqual(config["devices"], [])

    def test_load_config_creates_default_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = load_config(Path(tmp))

            self.assertEqual(config["poll_seconds"], 60)
            self.assertTrue((Path(tmp) / "config.json").exists())


class AuthTests(unittest.TestCase):
    def test_auth_token_accepts_bearer_or_custom_header(self):
        self.assertTrue(auth_token_valid("secret", "Bearer secret", None))
        self.assertTrue(auth_token_valid("secret", None, "secret"))

    def test_auth_token_rejects_missing_or_wrong_token(self):
        self.assertFalse(auth_token_valid("secret", None, None))
        self.assertFalse(auth_token_valid("secret", "Bearer wrong", None))


class ImageAssignmentTests(unittest.TestCase):
    def test_assign_image_rejects_wrong_orientation_before_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "portrait.png"
            write_png(image, 800, 1200)
            preview_writer = Mock()
            service = ManagerService(root=Path(tmp), config=SINGLE_DEVICE_CONFIG, preview_writer=preview_writer)

            result = service.assign_image("canvas-1", image)

            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["reason"], "orientation_mismatch")
            preview_writer.assert_not_called()

    def test_assign_image_promotes_file_only_after_successful_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "landscape.png"
            write_png(image, 1200, 800)
            preview_writer = Mock(return_value={"status": "pass"})
            service = ManagerService(root=Path(tmp), config=SINGLE_DEVICE_CONFIG, preview_writer=preview_writer)

            result = service.assign_image("canvas-1", image)

            self.assertEqual(result["status"], "loaded")
            self.assertTrue((Path(tmp) / "images" / "canvas-1.png").exists())


class StatusTests(unittest.TestCase):
    def test_summary_status_includes_reachability_from_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = ManagerService(root=Path(tmp), config=SINGLE_DEVICE_CONFIG)
            service.save_state(
                {
                    "devices": {
                        "canvas-1": {
                            "reachable": False,
                            "last_reachability_check_at": "2026-01-01T00:00:00+00:00",
                            "last_error": {"reason": "unreachable", "error": "timeout"},
                        }
                    }
                }
            )

            result = service.summary_status()

            self.assertEqual(result["device_count"], 1)
            self.assertEqual(result["reachable_count"], 0)
            self.assertFalse(result["devices"][0]["reachable"])

    def test_poll_once_records_unreachable_device_without_previewing(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "landscape.png"
            write_png(image, 1200, 800)
            config = {
                "reload_after_seconds": 0,
                "devices": [
                    {
                        "name": "canvas-1",
                        "display_name": "Canvas 1",
                        "cloud_id": None,
                        "local_ip": "192.0.2.10",
                        "orientation": "landscape",
                        "enabled": True,
                    }
                ],
            }
            service = ManagerService(
                root=Path(tmp),
                config=config,
                preview_writer=Mock(),
                reachability_checker=Mock(return_value=False),
            )
            service.images_dir.mkdir(exist_ok=True)
            target = service.images_dir / "canvas-1.png"
            target.write_bytes(image.read_bytes())

            result = service.poll_once()

            self.assertEqual(result["canvas-1"]["reason"], "unreachable")
            self.assertFalse(service.state()["devices"]["canvas-1"]["reachable"])


class InitTests(unittest.TestCase):
    def test_cloud_timeout_init_updates_and_syncs_configured_devices(self):
        cloud = Mock()
        config = {
            "timeouts": {"imageDuration": 86400, "previewDuration": 86400, "overlayDuration": 120},
            "devices": [
                {"name": "canvas-1", "cloud_id": 1001, "enabled": True},
                {"name": "spare-1", "cloud_id": 1002, "enabled": False},
                {"name": "unpaired", "cloud_id": None, "enabled": True},
            ],
        }

        result = initialise_cloud_timeouts(cloud, config, include_disabled=True)

        self.assertEqual(result["updated"], ["canvas-1", "spare-1"])
        cloud.update_device.assert_any_call(1001, config["timeouts"])
        cloud.sync_device.assert_any_call(1002)

    def test_blank_gallery_init_creates_orientation_specific_galleries(self):
        cloud = Mock()
        cloud.list_galleries.return_value = {"data": []}
        cloud.create_gallery.side_effect = [
            {"data": {"id": 2001, "name": "MeuralMCP Blank Hold Landscape"}},
            {"data": {"id": 2002, "name": "MeuralMCP Blank Hold Portrait"}},
        ]
        cloud.list_gallery_items.return_value = {"data": []}
        cloud.upload_item_to_gallery.side_effect = [{"data": {"id": 3001}}, {"data": {"id": 3002}}]
        cloud.list_device_galleries.return_value = {"data": []}
        config = {
            "blank_galleries": {
                "landscape": {
                    "name": "MeuralMCP Blank Hold Landscape",
                    "description": "test landscape",
                },
                "portrait": {
                    "name": "MeuralMCP Blank Hold Portrait",
                    "description": "test portrait",
                },
            },
            "devices": [
                {"name": "canvas-1", "cloud_id": 1001, "orientation": "landscape", "enabled": True},
                {"name": "canvas-2", "cloud_id": 1002, "orientation": "portrait", "enabled": True},
            ],
        }

        with tempfile.TemporaryDirectory() as tmp:
            result = ensure_blank_gallery_assigned(cloud, config, Path(tmp))

        self.assertEqual(result["galleries"]["landscape"]["gallery_id"], 2001)
        self.assertEqual(result["galleries"]["portrait"]["gallery_id"], 2002)
        self.assertEqual(result["galleries"]["landscape"]["assigned"], ["canvas-1"])
        self.assertEqual(result["galleries"]["portrait"]["assigned"], ["canvas-2"])
        cloud.set_device_gallery.assert_any_call(1001, 2001)
        cloud.set_device_gallery.assert_any_call(1002, 2002)


if __name__ == "__main__":
    unittest.main()
