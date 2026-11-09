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
        for command in ("init", "add-device", "serve", "mcp", "daemon", "publish-all", "service-install"):
            with self.subTest(command=command):
                self.assertIn(command, parser._subparsers._group_actions[0].choices)

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

    def test_add_device_replaces_same_name(self):
        main(["--storage-dir", str(self.root), "init", "--api-token", "abc"])
        for ip in ("192.0.2.10", "192.0.2.11"):
            main(["--storage-dir", str(self.root), "add-device", "--name", "canvas-1", "--ip", ip])
        config = json.loads((self.root / "config.json").read_text())
        self.assertEqual(len(config["devices"]), 1)
        self.assertEqual(config["devices"][0]["local_ip"], "192.0.2.11")


if __name__ == "__main__":
    unittest.main()
