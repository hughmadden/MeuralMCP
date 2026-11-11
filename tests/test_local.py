import unittest
from unittest.mock import Mock, call, patch

from meural_mcp.local import FrameLocalClient, FrameLocalError


class FrameLocalClientTests(unittest.TestCase):
    def test_base_url_always_uses_on_device_port(self):
        self.assertEqual(FrameLocalClient("192.0.2.10").base_url, "http://192.0.2.10:8099")
        self.assertEqual(
            FrameLocalClient("192.0.2.10", token="secret-token").base_url,
            "http://192.0.2.10:8099",
        )

    @patch("meural_mcp.local.requests.get")
    def test_requests_use_on_device_url_and_token(self, request_get):
        response = Mock(status_code=200, json=Mock(return_value={"is_ok": True}))
        request_get.return_value = response

        result = FrameLocalClient("192.0.2.10", token="secret-token").get("/local/status")

        self.assertEqual(result, {"is_ok": True})
        request_get.assert_called_once_with(
            "http://192.0.2.10:8099/local/status",
            timeout=5,
            params={"token": "secret-token"},
        )

    def test_reads_always_use_on_device_endpoints_without_token_fallback(self):
        client = FrameLocalClient("192.0.2.10")
        client.get = Mock(side_effect=[
            {"status": {"alias": "Canvas", "orientation": "landscape", "version": "1"}},
            {"status": {"current_gallery": "7", "current_item": "8"}},
            {"gallery": {"items": [{"id": "9"}]}},
        ])

        self.assertEqual(client.identify()["alias"], "Canvas")
        self.assertEqual(
            client.current_gallery(),
            {"current_gallery": "7", "current_item": "8"},
        )
        self.assertEqual(client.gallery_items("7"), [{"id": "9"}])
        self.assertEqual(
            client.get.call_args_list,
            [call("/local/status"), call("/local/status"), call("/local/gallery/7")],
        )

    def test_actions_always_use_on_device_endpoints_without_token_fallback(self):
        client = FrameLocalClient("192.0.2.10")
        client.post = Mock(return_value={"is_ok": True})

        client.change_gallery("7")
        client.change_item("8")
        client.sleep()
        client.wake()

        self.assertEqual(
            client.post.call_args_list,
            [
                call("/local/show/gallery/7"),
                call("/local/show/item/8"),
                call("/local/action", data={"do": "suspend"}),
                call("/local/action", data={"do": "resume"}),
            ],
        )


class FrameLocalErrorTests(unittest.TestCase):
    def test_frame_token_is_redacted_from_error_url_and_body(self):
        client = FrameLocalClient("192.0.2.10", token="secret-token")
        response = Mock(
            status_code=403,
            url="http://192.0.2.10:8099/local/status?token=secret-token",
            text='{"error":"bad token secret-token"}',
        )

        with self.assertRaises(FrameLocalError) as caught:
            client._check(response)

        self.assertEqual(caught.exception.status, 403)
        self.assertNotIn("secret-token", str(caught.exception))
        self.assertIn("<redacted>", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
