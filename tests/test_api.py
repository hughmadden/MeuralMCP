import unittest

from fastapi import HTTPException

from meural_mcp.api import require_api_token


class ApiAuthTests(unittest.TestCase):
    def test_require_api_token_accepts_bearer(self):
        require_api_token("secret", authorization="Bearer secret", x_meural_mcp_token=None)

    def test_require_api_token_rejects_missing(self):
        with self.assertRaises(HTTPException) as ctx:
            require_api_token("secret", authorization=None, x_meural_mcp_token=None)

        self.assertEqual(ctx.exception.status_code, 401)


if __name__ == "__main__":
    unittest.main()

