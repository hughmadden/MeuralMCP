import json
import tempfile
import unittest
from pathlib import Path

from meural_mcp.cli import build_parser, main


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_parser_has_expected_commands(self):
        parser = build_parser()
        commands = parser._subparsers._group_actions[0].choices
        for command in ("init", "add-device", "serve", "mcp", "daemon", "publish-all"):
            with self.subTest(command=command):
                self.assertIn(command, commands)
        self.assertNotIn("service-install", commands)
        self.assertNotIn("--product-key", commands["add-device"]._option_string_actions)

    def test_init_writes_token(self):
        self.assertEqual(main(["--storage-dir", str(self.root), "init", "--api-token", "abc"]), 0)
        config = json.loads((self.root / "config.json").read_text())
        self.assertEqual(config["api_token"], "abc")

    def test_add_device(self):
        main(["--storage-dir", str(self.root), "init", "--api-token", "abc"])
        code = main([
            "--storage-dir", str(self.root), "add-device",
            "--name", "Canvas One", "--ip", "192.0.2.10", "--orientation", "portrait",
        ])
        self.assertEqual(code, 0)
        config = json.loads((self.root / "config.json").read_text())
        self.assertEqual(config["devices"][0]["name"], "canvas-one")
        self.assertEqual(config["devices"][0]["orientation"], "portrait")
        self.assertNotIn("product_key", config["devices"][0])

    def test_add_device_replaces_same_name(self):
        main(["--storage-dir", str(self.root), "init", "--api-token", "abc"])
        for ip in ("192.0.2.10", "192.0.2.11"):
            main(["--storage-dir", str(self.root), "add-device", "--name", "canvas-1", "--ip", ip])
        config = json.loads((self.root / "config.json").read_text())
        self.assertEqual(len(config["devices"]), 1)
        self.assertEqual(config["devices"][0]["local_ip"], "192.0.2.11")

    def test_add_device_token_preserves_existing_fields(self):
        main(["--storage-dir", str(self.root), "init", "--api-token", "abc"])
        main(["--storage-dir", str(self.root), "add-device", "--name", "canvas-1", "--ip", "192.0.2.10", "--orientation", "portrait"])
        path = self.root / "config.json"
        config = json.loads(path.read_text())
        config["devices"][0].update({
            "enabled": False,
            "custom_field": "preserved",
            "schedule": {"enabled": True, "timezone": "Australia/Sydney", "sleep_start": "19:30", "wake_time": "07:00"},
        })
        path.write_text(json.dumps(config))

        main(["--storage-dir", str(self.root), "add-device", "--name", "canvas-1", "--ip", "192.0.2.11", "--token", "frame-token"])
        device = json.loads(path.read_text())["devices"][0]
        self.assertEqual(device["local_ip"], "192.0.2.11")
        self.assertEqual(device["api_token"], "frame-token")
        self.assertEqual(device["orientation"], "portrait")
        self.assertFalse(device["enabled"])
        self.assertEqual(device["custom_field"], "preserved")
        self.assertNotIn("product_key", device)
        self.assertEqual(device["schedule"]["sleep_start"], "19:30")

        main(["--storage-dir", str(self.root), "add-device", "--name", "canvas-1", "--ip", "192.0.2.12"])
        device = json.loads(path.read_text())["devices"][0]
        self.assertEqual(device["api_token"], "frame-token")
        self.assertEqual(device["orientation"], "portrait")


if __name__ == "__main__":
    unittest.main()
