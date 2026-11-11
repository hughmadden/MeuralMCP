import json
import struct
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

from meural_mcp.manager import (
    ManagerService,
    canonical_name,
    default_config,
    image_dimensions,
    load_config,
    normalise_orientation,
)


def png_bytes(width: int, height: int) -> bytes:
    # IHDR only; enough for dimension parsing
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", width, height) + b"\x00" * 9


class ManagerTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.client = Mock()
        self.client.identify.return_value = {"alias": "Canvas 1", "orientation": "landscape"}
        self.client.current_gallery.return_value = {"current_gallery": "1"}
        self.client.gallery_exists.return_value = False
        self.client.create_gallery.return_value = "5555"
        self.client.upload_item.return_value = "777"
        self.client.gallery_items.return_value = [{"id": "777"}]
        self.client.change_gallery.return_value = {"is_ok": True}
        self.client.change_item.return_value = {"is_ok": True}
        self.client.delete_item.return_value = {"is_ok": True}
        self.client.sleep.return_value = {"is_ok": True}
        self.client.wake.return_value = {"is_ok": True}

        self.service = ManagerService(
            root=self.root,
            config={
                "devices": [{
                    "name": "canvas-1", "display_name": "Canvas 1", "local_ip": "192.0.2.10",
                    "orientation": "landscape", "enabled": True, "api_token": "frame-token",
                }],
            },
            local_client_factory=lambda ip, timeout=5: self.client,
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_publish_uses_device_api(self):
        calls = []
        self.client.gallery_items.return_value = [{"id": "777"}, {"id": "666"}]
        self.client.change_gallery.side_effect = lambda gid: calls.append(("show", gid))
        self.client.change_item.side_effect = lambda iid: calls.append(("select", iid))
        self.client.delete_item.side_effect = lambda iid: calls.append(("del", iid))

        status = self.service.device_status("canvas-1")
        self.assertIsNone(status["gallery_id"])
        self.assertNotIn("product_key", status)

        self.service.set_image("canvas-1", png_bytes(1920, 1080), "a.png")
        result = self.service.publish("canvas-1")

        self.assertEqual(result["status"], "pass")
        self.assertTrue(result.get("device"))
        self.assertEqual(result["gallery_id"], "5555")
        # The stale item is removed, and the new gallery and item are shown.
        self.assertIn(("del", "666"), calls)
        self.assertIn(("show", "5555"), calls)
        self.assertIn(("select", "777"), calls)
        self.client.upload_item.assert_any_call(
            "5555", self.service.image_path("canvas-1"), title="Canvas 1"
        )
        self.assertEqual(self.service.device_status("canvas-1")["gallery_id"], "5555")

        # Cleanup failure is not a false pass; the new item is still selected.
        self.client.delete_item.side_effect = RuntimeError("delete failed")
        with self.assertRaisesRegex(RuntimeError, "delete failed"):
            self.service.publish("canvas-1")
        self.assertEqual(calls[-1], ("select", "777"))

    def test_publish_requires_api_token(self):
        factory = Mock()
        service = ManagerService(
            root=self.root / "tokenless",
            config={"devices": [{"name": "canvas-2", "orientation": "landscape"}]},
            local_client_factory=factory,
        )
        (service.images_dir / "canvas-2.png").write_bytes(png_bytes(1920, 1080))

        self.assertEqual(
            service.publish("canvas-2"),
            {"status": "failed", "reason": "on_device_api_required"},
        )
        factory.assert_not_called()
        self.assertIsNone(service.device_status("canvas-2")["gallery_id"])

    def test_summary_contains_only_device_counts(self):
        self.service.config["devices"].append({"name": "canvas-2", "enabled": False})
        expected = {"devices_total": 2, "devices_enabled": 1}
        self.assertEqual(self.service.summary(), expected)
        self.assertEqual(self.service.summary_status(), expected)

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

    def test_sleep_and_wake(self):
        self.assertEqual(self.service.sleep("canvas-1")["status"], "pass")
        self.assertEqual(self.service.wake("canvas-1")["status"], "pass")
        self.client.sleep.assert_called_once_with()
        self.client.wake.assert_called_once_with()


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
        self.service.now_provider = lambda: datetime(2026, 1, 1, 23, 0, tzinfo=timezone.utc)
        self.assertEqual(self.service.apply_sleep_schedules()[0]["asleep"], True)
        self.client.sleep.assert_called_once()
        self.service.now_provider = lambda: datetime(2026, 1, 2, 9, 0, tzinfo=timezone.utc)
        self.assertEqual(self.service.apply_sleep_schedules()[0]["asleep"], False)
        self.client.wake.assert_called_once()

    def test_daemon_retries_after_frame_rejects_sleep(self):
        self.service.set_schedule("canvas-1", "22:00", "07:00", "Etc/UTC")
        self.service.now_provider = lambda: datetime(2026, 1, 1, 23, 0, tzinfo=timezone.utc)
        self.client.sleep.return_value = {"is_ok": False, "error": "busy"}
        self.assertEqual(self.service.apply_sleep_schedules(), [])
        entry = self.service.state()["devices"]["canvas-1"]
        self.assertNotIn("asleep", entry)
        self.assertEqual(entry["sleep_error"], "on_device_api_failed")

        self.client.sleep.return_value = {"is_ok": True}
        self.assertEqual(self.service.apply_sleep_schedules()[0]["asleep"], True)
        entry = self.service.state()["devices"]["canvas-1"]
        self.assertTrue(entry["asleep"])
        self.assertNotIn("sleep_error", entry)

    def test_daemon_does_not_mark_tokenless_sleep_as_success(self):
        self.service.config["devices"][0].pop("api_token")
        self.service.set_schedule("canvas-1", "22:00", "07:00", "Etc/UTC")
        self.service.now_provider = lambda: datetime(2026, 1, 1, 23, 0, tzinfo=timezone.utc)
        self.assertEqual(self.service.apply_sleep_schedules(), [])
        entry = self.service.state()["devices"]["canvas-1"]
        self.assertNotIn("asleep", entry)
        self.assertEqual(entry["sleep_error"], "on_device_api_required")
        self.client.sleep.assert_not_called()

    def test_disabled_schedule_ignored(self):
        self.service.set_schedule("canvas-1", "22:00", "07:00", enabled=False)
        self.assertEqual(self.service.apply_sleep_schedules(), [])


class ConfigTests(unittest.TestCase):
    def test_default_and_loaded_config_omit_legacy_service(self):
        self.assertNotIn("service", default_config())
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config.json").write_text(json.dumps({
                "poll_seconds": 15,
                "service": {"root": "/legacy", "port": 443},
                "devices": [],
            }))
            config = load_config(root)
        self.assertEqual(config["poll_seconds"], 15)
        self.assertNotIn("service", config)


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
