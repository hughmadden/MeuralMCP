import unittest
from unittest.mock import Mock

from meural_mcp.local import FrameLocalClient, FrameLocalError


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
