import base64
import unittest
from unittest.mock import Mock, patch

from meural_mcp.mcp_server import (
    mcp_create_device_gallery,
    mcp_device_time,
    mcp_get_device_settings,
    mcp_list_device_galleries,
    mcp_run_device_action,
    mcp_set_device_picture,
    mcp_update_device_settings,
    mcp_upload_device_gallery_item,
)


class FeatureMcpTests(unittest.TestCase):
    def setUp(self):
        self.service = Mock()
        for name in (
            "device_time", "list_galleries", "create_gallery", "get_settings",
            "update_settings", "run_action", "set_picture", "upload_gallery_item",
        ):
            getattr(self.service, name).return_value = {"status": "pass"}
        self.remote_patch = patch("meural_mcp.mcp_server._remote_client", return_value=None)
        self.remote_patch.start()
        self.service_patch = patch("meural_mcp.mcp_server._service", return_value=self.service)
        self.service_patch.start()

    def tearDown(self):
        self.service_patch.stop()
        self.remote_patch.stop()

    def test_read_and_write_features_delegate_to_manager(self):
        self.assertEqual(mcp_device_time("canvas-1")["status"], "pass")
        mcp_list_device_galleries("canvas-1")
        self.service.list_galleries.assert_called_with("canvas-1")
        mcp_create_device_gallery("canvas-1", "Gallery")
        self.service.create_gallery.assert_called_with("canvas-1", gallery_name="Gallery")
        mcp_get_device_settings("canvas-1", include_sensitive=True)
        self.service.get_settings.assert_called_with("canvas-1", include_sensitive=True)
        mcp_update_device_settings("canvas-1", {"brightness": "40"})
        self.service.update_settings.assert_called_with("canvas-1", values={"brightness": "40"})
        mcp_run_device_action("canvas-1", "resume")
        self.service.run_action.assert_called_with("canvas-1", action="resume", value=None, value2=None)
        mcp_set_device_picture("canvas-1", "/tmp/a.jpg", timeout=0)
        self.service.set_picture.assert_called_with("canvas-1", path="/tmp/a.jpg", timeout=0)

    def test_upload_decodes_base64_for_manager(self):
        result = mcp_upload_device_gallery_item("canvas-1", "55", base64.b64encode(b"image").decode(), "a.png", "Poster")
        self.assertEqual(result["status"], "pass")
        self.service.upload_gallery_item.assert_called_with(
            "canvas-1", "55", data=b"image", filename=".png", title="Poster")

    def test_upload_uses_remote_api_client_when_configured(self):
        remote = Mock()
        remote.upload_gallery_item.return_value = {"status": "pass", "item_id": "66"}
        self.remote_patch.stop()
        self.remote_patch = patch("meural_mcp.mcp_server._remote_client", return_value=remote)
        self.remote_patch.start()
        result = mcp_upload_device_gallery_item("canvas-1", "55", base64.b64encode(b"image").decode())
        self.assertEqual(result["item_id"], "66")
        remote.upload_gallery_item.assert_called_with(
            "canvas-1", "55", image_bytes=b"image", suffix=".png", title=None)

    def test_bad_base64_is_rejected(self):
        result = mcp_upload_device_gallery_item("canvas-1", "55", "!!!")
        self.assertEqual(result["reason"], "image_base64_invalid")


if __name__ == "__main__":
    unittest.main()
