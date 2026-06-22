import unittest
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from meural_mcp.api import create_app, require_api_token


class ApiAuthTests(unittest.TestCase):
    def test_require_api_token_accepts_bearer(self):
        require_api_token("secret", authorization="Bearer secret", x_meural_mcp_token=None)

    def test_require_api_token_rejects_missing(self):
        with self.assertRaises(HTTPException) as ctx:
            require_api_token("secret", authorization=None, x_meural_mcp_token=None)

        self.assertEqual(ctx.exception.status_code, 401)

    def test_remote_mcp_requires_token(self):
        with patch("meural_mcp.api.load_config", return_value={"api_token": "secret"}):
            with TestClient(create_app()) as client:
                response = client.post("/mcp")

        self.assertEqual(response.status_code, 401)

    def test_remote_mcp_accepts_token_before_protocol_validation(self):
        headers = {"Authorization": "Bearer secret"}
        with patch("meural_mcp.api.load_config", return_value={"api_token": "secret"}):
            with TestClient(create_app()) as client:
                response = client.post("/mcp", headers=headers)

        self.assertNotEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
