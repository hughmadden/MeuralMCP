import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import Mock

from meural_mcp.manager import ManagerService, canonical_name, image_dimensions, normalise_orientation


def png_bytes(width: int, height: int) -> bytes:
    # IHDR only; enough for dimension parsing
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", width, height) + b"\x00" * 9


class ManagerTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.service_root = self.root / "svc"
        for sub in ("media", "by-id", "queues"):
            (self.service_root / sub).mkdir(parents=True)
        self.identify_calls = []

        def factory(ip, timeout=5):
            client = Mock()
            self.identify_calls.append(ip)
            client.identify.return_value = {"product_key": f"KEY-{ip}", "orientation": "landscape"}
            client.current_gallery.return_value = {"current_gallery": "1"}
            return client

        self.service = ManagerService(
            root=self.root,
            config={
                "service": {"root": str(self.service_root), "port": 443, "bind": "0.0.0.0"},
                "devices": [{
                    "name": "canvas-1", "display_name": "Canvas 1", "local_ip": "192.0.2.10",
                    "orientation": "landscape", "enabled": True, "product_key": "KEY-1",
                }],
            },
            local_client_factory=factory,
            thumbnailer=lambda src, dst, orientation: dst.write_bytes(b"thumb"),
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_publish_writes_gallery_and_queues_command(self):
        self.service.set_image("canvas-1", png_bytes(1920, 1080), "a.png")
        result = self.service.publish("canvas-1")

        self.assertEqual(result["status"], "pass")
        gid = result["gallery_id"]
        gallery = json.loads((self.service_root / "by-id" / f"{gid}.json").read_text())
        item = gallery["items"][0]
        self.assertEqual(gallery["orientation"], 1)
        self.assertEqual(item["orientation"], "landscape")
        self.assertTrue(item["checksum"])
        self.assertEqual(item["size"], (self.service_root / "media" / "canvas-1.png").stat().st_size)

        queued = json.loads((self.service_root / "queues" / "KEY-1.json").read_text())
        self.assertEqual(queued[0]["command"], "get_galleries")
        self.assertEqual(queued[0]["parameter"], gid)

    def test_publish_uses_device_api_when_token_present(self):
        """With an api_token, publish goes to the frame and queues nothing."""
        calls = []

        def factory(ip, timeout=5):
            client = Mock()
            client.identify.return_value = {"product_key": "KEY-1", "orientation": "landscape"}
            client.gallery_exists.return_value = False
            client.create_gallery.return_value = "5555"
            client.upload_item.return_value = "777"
            client.gallery_items.return_value = [{"id": "777"}, {"id": "666"}]
            client.change_gallery.side_effect = lambda gid: calls.append(("show", gid))
            client.delete_item.side_effect = lambda iid: calls.append(("del", iid))
            return client

        service = ManagerService(
            root=self.root,
            config={
                "service": {"root": str(self.service_root), "port": 443, "bind": "0.0.0.0"},
                "devices": [{
                    "name": "canvas-1", "display_name": "Canvas 1", "local_ip": "192.0.2.10",
                    "orientation": "landscape", "enabled": True,
                    "product_key": "KEY-1", "api_token": "tok",
                }],
            },
            local_client_factory=factory,
            thumbnailer=lambda src, dst, orientation: dst.write_bytes(b"thumb"),
        )
        service.set_image("canvas-1", png_bytes(1920, 1080), "a.png")
        result = service.publish("canvas-1")

        self.assertEqual(result["status"], "pass")
        self.assertTrue(result.get("device"))
        self.assertEqual(result["gallery_id"], "5555")
        # the stale item is removed, the new one shown
        self.assertIn(("del", "666"), calls)
        self.assertIn(("show", "5555"), calls)
        self.assertFalse((self.service_root / "queues" / "KEY-1.json").exists())

    def test_gallery_id_is_stable(self):
        self.assertEqual(self.service.gallery_id("canvas-1"), self.service.gallery_id("Canvas_1"))

    def test_orientation_mismatch_rejected(self):
        result = self.service.set_image("canvas-1", png_bytes(1080, 1920), "p.png")
        self.assertEqual(result["reason"], "orientation_mismatch")
        self.assertIsNone(self.service.image_path("canvas-1"))

    def test_unreadable_image_rejected(self):
        result = self.service.set_image("canvas-1", b"not an image", "x.jpg")
        self.assertEqual(result["reason"], "unreadable_image")

    def test_disabled_device_rejected(self):
        self.service.config["devices"][0]["enabled"] = False
        result = self.service.set_image("canvas-1", png_bytes(1920, 1080), "a.png")
        self.assertEqual(result["reason"], "disabled")

    def test_clear_image(self):
        self.service.set_image("canvas-1", png_bytes(1920, 1080), "a.png")
        self.assertEqual(self.service.clear_image("canvas-1")["status"], "pass")
        self.assertIsNone(self.service.image_path("canvas-1"))

    def test_unknown_device(self):
        with self.assertRaises(KeyError):
            self.service.device("nope")

    def test_product_key_discovered_and_saved(self):
        self.service.config["devices"][0]["product_key"] = None
        self.service.config["devices"][0]["local_ip"] = "192.0.2.11"
        self.service.product_key(self.service.device("canvas-1"))
        self.assertIn("192.0.2.11", self.identify_calls)
        self.assertTrue((self.root / "config.json").exists())

    def test_sleep_and_wake(self):
        self.assertEqual(self.service.sleep("canvas-1")["status"], "pass")
        self.assertEqual(self.service.wake("canvas-1")["status"], "pass")


class ScheduleTests(ManagerTestCase):
    def test_set_and_get_schedule(self):
        self.assertEqual(self.service.set_schedule("canvas-1", "22:30", "07:00", "Etc/UTC")["status"], "pass")
        schedule = self.service.get_schedule("canvas-1")
        self.assertEqual(schedule["sleep_start"], "22:30")
        self.assertTrue(schedule["enabled"])

    def test_invalid_time_rejected(self):
        self.assertEqual(self.service.set_schedule("canvas-1", "25:00", "07:00")["reason"], "invalid_time")

    def test_identical_times_rejected(self):
        self.assertEqual(self.service.set_schedule("canvas-1", "07:00", "07:00")["reason"], "identical_times")

    def test_unknown_timezone_rejected(self):
        self.assertEqual(self.service.set_schedule("canvas-1", "22:00", "07:00", "Mars/Base")["reason"], "unknown_timezone")

    def test_clear_schedule(self):
        self.service.set_schedule("canvas-1", "22:00", "07:00")
        self.assertEqual(self.service.clear_schedule("canvas-1")["status"], "pass")
        self.assertFalse(self.service.get_schedule("canvas-1")["enabled"])

    def test_daemon_edge_applies_sleep_then_wake(self):
        self.service.set_schedule("canvas-1", "22:00", "07:00", "Etc/UTC")
        client = Mock()
        self.service.local_client_factory = lambda ip, timeout=5: client
        from datetime import datetime, timezone
        self.service.now_provider = lambda: datetime(2026, 1, 1, 23, 0, tzinfo=timezone.utc)
        self.assertEqual(self.service.apply_sleep_schedules()[0]["asleep"], True)
        client.sleep.assert_called_once()
        self.service.now_provider = lambda: datetime(2026, 1, 2, 9, 0, tzinfo=timezone.utc)
        self.assertEqual(self.service.apply_sleep_schedules()[0]["asleep"], False)
        client.wake.assert_called_once()

    def test_disabled_schedule_ignored(self):
        self.service.set_schedule("canvas-1", "22:00", "07:00", enabled=False)
        self.assertEqual(self.service.apply_sleep_schedules(), [])


class HelperTests(unittest.TestCase):
    def test_canonical_name(self):
        self.assertEqual(canonical_name(" Canvas_One "), "canvas-one")

    def test_normalise_orientation(self):
        self.assertEqual(normalise_orientation("horizontal"), "landscape")
        self.assertEqual(normalise_orientation("portrait"), "portrait")

    def test_image_dimensions_png(self):
        self.assertEqual(image_dimensions_bytes(png_bytes(1080, 1920)), (1080, 1920))


def image_dimensions_bytes(data: bytes):
    with tempfile.NamedTemporaryFile(suffix=".png") as tmp:
        tmp.write(data)
        tmp.flush()
        return image_dimensions(Path(tmp.name))


if __name__ == "__main__":
    unittest.main()
