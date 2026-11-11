import copy
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, call

from meural_mcp.local import FrameLocalClient
from meural_mcp.manager import ManagerService, image_dimensions


def png_bytes(width=320, height=200):
    return (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13)
        + b"IHDR"
        + struct.pack(">II", width, height)
        + b"\x00" * 9
    )


def jpeg_bytes(width=320, height=200):
    # SOI and a complete baseline SOF0 segment; enough for the existing parser.
    components = b"\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00"
    return b"\xff\xd8\xff\xc0\x00\x11\x08" + struct.pack(">HH", height, width) + components


class FrameLocalClientFeatureTests(unittest.TestCase):
    def setUp(self):
        self.client = FrameLocalClient("192.0.2.10", token="test-token")
        self.client.get = Mock(return_value={"is_ok": True})
        self.client.post = Mock(return_value={"is_ok": True})

    def test_time_settings_and_gallery_reads_use_full_responses(self):
        self.client.get.side_effect = lambda path: {"path": path, "is_ok": True}

        self.assertEqual(self.client.time()["path"], "/api/v3/time")
        self.assertEqual(self.client.settings()["path"], "/local/settings")
        self.assertEqual(self.client.settings_schema()["path"], "/local/settings/schema")
        self.assertEqual(self.client.list_galleries()["path"], "/local/list")
        self.assertEqual(self.client.gallery("42")["path"], "/local/gallery/42")
        self.assertEqual(
            self.client.get.call_args_list,
            [
                call("/api/v3/time"),
                call("/local/settings"),
                call("/local/settings/schema"),
                call("/local/list"),
                call("/local/gallery/42"),
            ],
        )

    def test_settings_action_and_picture_posts(self):
        values = {"brightness": 20}
        self.client.update_settings(values)
        self.client.action("suspend")
        self.client.action("custom", value=0, value2=False)
        self.client.set_picture("/frame/local/image.jpg", timeout=12)

        self.assertEqual(
            self.client.post.call_args_list,
            [
                call("/local/settings", data=values),
                call("/local/action", data={"do": "suspend"}),
                call(
                    "/local/action",
                    data={"do": "custom", "value": 0, "value2": False},
                ),
                call(
                    "/local/setpic",
                    data={"path": "/frame/local/image.jpg", "timeout": 12},
                ),
            ],
        )


class ManagerFeatureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.client = Mock()
        self.timeouts = []

        def factory(ip, timeout=5):
            self.timeouts.append(timeout)
            return self.client

        self.service = ManagerService(
            root=self.root,
            config={
                "devices": [
                    {
                        "name": "canvas-1",
                        "local_ip": "192.0.2.10",
                        "api_token": "test-frame-token",
                    },
                    {"name": "canvas-2", "local_ip": "192.0.2.11"},
                ],
            },
            local_client_factory=factory,
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_device_time_list_and_get_gallery(self):
        self.client.time.return_value = {"is_ok": True, "time": "2026-01-02T03:04:05Z"}
        self.client.list_galleries.return_value = {"is_ok": True, "galleries": [{"id": 7}]}
        self.client.gallery.return_value = {"is_ok": True, "gallery": {"id": 7}}

        self.assertEqual(self.service.device_time("canvas-1")["status"], "pass")
        self.assertEqual(self.service.device_time("canvas-1")["time"], "2026-01-02T03:04:05Z")
        galleries = self.service.list_galleries("canvas-1")
        self.assertEqual(galleries["galleries"], [{"id": 7}])
        gallery = self.service.get_gallery("canvas-1", 7)
        self.assertEqual(gallery["gallery"], {"id": 7})
        self.client.gallery.assert_called_once_with(7)

    def test_create_gallery_returns_id_and_validates_name(self):
        self.client.create_gallery.return_value = "81"
        result = self.service.create_gallery("canvas-1", "New gallery")
        self.assertEqual(result, {"status": "pass", "gallery_id": "81"})
        self.client.create_gallery.assert_called_once_with("New gallery")

        self.client.reset_mock()
        self.assertEqual(
            self.service.create_gallery("canvas-1", "  ")["reason"],
            "invalid_gallery_name",
        )
        self.client.create_gallery.assert_not_called()

    def test_upload_gallery_item_validates_dimensions_and_cleans_temp_files(self):
        uploaded_paths = []

        def upload(gallery_id, path, title=None):
            self.assertEqual(gallery_id, "9")
            self.assertEqual(title, "A title")
            self.assertTrue(path.exists())
            self.assertEqual(image_dimensions(path), (320, 200))
            uploaded_paths.append(path)
            return f"item-{len(uploaded_paths)}"

        self.client.upload_item.side_effect = upload
        first = self.service.upload_gallery_item(
            "canvas-1", "9", png_bytes(), filename="photo.png", title="A title"
        )
        second = self.service.upload_gallery_item(
            "canvas-1", "9", png_bytes(), filename="photo.png", title="A title"
        )

        self.assertEqual(first, {"status": "pass", "item_id": "item-1"})
        self.assertEqual(second, {"status": "pass", "item_id": "item-2"})
        self.assertEqual(len(uploaded_paths), 2)
        self.assertNotEqual(uploaded_paths[0], uploaded_paths[1])
        self.assertTrue(all(not path.exists() for path in uploaded_paths))
        self.assertEqual(list(self.service.images_dir.glob(".gallery-upload-*")), [])

    def test_upload_gallery_item_accepts_jpeg_without_filename(self):
        uploaded = []

        def upload(gallery_id, path, title=None):
            uploaded.append(path)
            self.assertEqual(path.suffix, ".jpg")
            self.assertEqual(image_dimensions(path), (640, 480))
            return "jpeg-item"

        self.client.upload_item.side_effect = upload
        result = self.service.upload_gallery_item("canvas-1", 3, jpeg_bytes(640, 480))
        self.assertEqual(result["item_id"], "jpeg-item")
        self.assertFalse(uploaded[0].exists())

    def test_invalid_upload_is_rejected_and_cleaned_up(self):
        result = self.service.upload_gallery_item(
            "canvas-1", 3, b"not an image", filename="bad.jpg"
        )
        self.assertEqual(result, {"status": "failed", "reason": "unreadable_image"})
        self.client.upload_item.assert_not_called()
        self.assertEqual(list(self.service.images_dir.glob(".gallery-upload-*")), [])

    def test_upload_exception_still_cleans_temp_file(self):
        self.client.upload_item.side_effect = RuntimeError("upload failed")
        with self.assertRaisesRegex(RuntimeError, "upload failed"):
            self.service.upload_gallery_item("canvas-1", 3, png_bytes(), "photo.png")
        self.assertEqual(list(self.service.images_dir.glob(".gallery-upload-*")), [])

    def test_show_and_delete_gallery_and_item(self):
        for method in ("change_gallery", "change_item", "delete_gallery", "delete_item"):
            getattr(self.client, method).return_value = {"is_ok": True}

        self.assertEqual(self.service.show_gallery("canvas-1", 4)["status"], "pass")
        self.assertEqual(self.service.show_item("canvas-1", 5)["status"], "pass")
        self.assertEqual(self.service.delete_gallery("canvas-1", 4)["status"], "pass")
        self.assertEqual(self.service.delete_item("canvas-1", 5)["status"], "pass")
        self.client.change_gallery.assert_called_once_with(4)
        self.client.change_item.assert_called_once_with(5)
        self.client.delete_gallery.assert_called_once_with(4)
        self.client.delete_item.assert_called_once_with(5)

    def test_set_picture_validates_input_and_uses_operation_timeout(self):
        self.client.set_picture.return_value = {"is_ok": True}
        result = self.service.set_picture("canvas-1", "/frame/image.jpg")
        self.assertEqual(result["status"], "pass")
        self.client.set_picture.assert_called_once_with("/frame/image.jpg", timeout=600)
        self.assertEqual(self.timeouts[-1], 610)

        self.service.set_picture("canvas-1", "/frame/image.jpg", timeout=0)
        self.assertEqual(self.timeouts[-1], 60)
        for path, timeout in (("", 1), ("  ", 1), ("/frame/image.jpg", -1),
                              ("/frame/image.jpg", 1.5), ("/frame/image.jpg", True)):
            with self.subTest(path=path, timeout=timeout):
                self.assertEqual(self.service.set_picture("canvas-1", path, timeout)["status"], "failed")

    def test_settings_are_recursively_redacted_by_default_and_raw_on_explicit_true(self):
        raw = {
            "is_ok": True,
            "settings": {
                "pass": "one",
                "password": "two",
                "api_token": "three",
                "client_secret": "four",
                "captive_user_value": "five",
                "ordinary": "visible",
                "nested": [{"wifi_password": "six", "name": "still-visible"}],
            },
        }
        original = copy.deepcopy(raw)
        self.client.settings.return_value = raw

        redacted = self.service.get_settings("canvas-1")
        settings = redacted["settings"]
        for key in ("pass", "password", "api_token", "client_secret", "captive_user_value"):
            self.assertEqual(settings[key], "<redacted>")
        self.assertEqual(settings["ordinary"], "visible")
        self.assertEqual(settings["nested"][0]["wifi_password"], "<redacted>")
        self.assertEqual(settings["nested"][0]["name"], "still-visible")
        self.assertEqual(raw, original, "redaction must not mutate the client response")

        self.assertEqual(
            self.service.get_settings("canvas-1", include_sensitive=True),
            {**original, "status": "pass"},
        )

    def test_settings_schema(self):
        self.client.settings_schema.return_value = {
            "is_ok": True,
            "schema": {"brightness": {"type": "integer"}},
        }
        result = self.service.get_settings_schema("canvas-1")
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["schema"]["brightness"]["type"], "integer")

    def test_update_settings_status_mapping(self):
        cases = [
            ({"applied": ["brightness"], "rejected": [], "unknown": []}, "pass"),
            ({"applied": ["brightness"], "rejected": ["bad"], "unknown": []}, "partial"),
            ({"applied": ["brightness"], "rejected": [], "unknown": ["other"]}, "partial"),
            ({"applied": [], "rejected": ["bad"], "unknown": []}, "failed"),
            ({"applied": [], "rejected": [], "unknown": ["other"]}, "failed"),
            ({"applied": [], "rejected": [], "unknown": []}, "failed"),
        ]
        values = {"brightness": 20}
        for response, expected in cases:
            with self.subTest(expected=expected, response=response):
                self.client.update_settings.return_value = {"is_ok": True, **response}
                result = self.service.update_settings("canvas-1", values)
                self.assertEqual(result["status"], expected)
                self.client.update_settings.assert_called_with(values)

        for invalid in ({}, None, []):
            with self.subTest(invalid=invalid):
                self.assertEqual(
                    self.service.update_settings("canvas-1", invalid)["reason"],
                    "invalid_settings",
                )

    def test_run_action_forwards_unfiltered_values_and_validates_action(self):
        self.client.action.return_value = {"is_ok": True}
        value = {"level": 0}
        value2 = [False, "two"]
        result = self.service.run_action("canvas-1", "vendor-specific", value, value2)
        self.assertEqual(result["status"], "pass")
        self.client.action.assert_called_once_with("vendor-specific", value, value2)

        self.client.reset_mock()
        self.assertEqual(self.service.run_action("canvas-1", " ")["reason"], "invalid_action")
        self.client.action.assert_not_called()

    def test_is_ok_false_is_a_failed_result(self):
        self.client.time.return_value = {"is_ok": False, "error": "frame rejected request"}
        result = self.service.device_time("canvas-1")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason"], "on_device_api_failed")
        self.assertEqual(result["error"], "frame rejected request")

    def test_every_feature_requires_an_on_device_token(self):
        calls = [
            lambda: self.service.device_time("canvas-2"),
            lambda: self.service.list_galleries("canvas-2"),
            lambda: self.service.get_gallery("canvas-2", 1),
            lambda: self.service.create_gallery("canvas-2", "Gallery"),
            lambda: self.service.upload_gallery_item("canvas-2", 1, png_bytes()),
            lambda: self.service.show_gallery("canvas-2", 1),
            lambda: self.service.show_item("canvas-2", 2),
            lambda: self.service.delete_gallery("canvas-2", 1),
            lambda: self.service.delete_item("canvas-2", 2),
            lambda: self.service.set_picture("canvas-2", "/frame/image.jpg"),
            lambda: self.service.get_settings("canvas-2"),
            lambda: self.service.get_settings_schema("canvas-2"),
            lambda: self.service.update_settings("canvas-2", {"brightness": 1}),
            lambda: self.service.run_action("canvas-2", "resume"),
            lambda: self.service.sleep("canvas-2"),
            lambda: self.service.wake("canvas-2"),
        ]
        self.timeouts.clear()
        for feature_call in calls:
            with self.subTest(feature_call=feature_call):
                self.assertEqual(
                    feature_call(),
                    {"status": "failed", "reason": "on_device_api_required"},
                )
        self.assertEqual(self.timeouts, [], "tokenless calls must not create a frame client")
        self.assertEqual(list(self.service.images_dir.glob(".gallery-upload-*")), [])


if __name__ == "__main__":
    unittest.main()
