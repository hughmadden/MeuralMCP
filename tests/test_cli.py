import unittest
import tempfile
import json
from pathlib import Path
from unittest.mock import Mock, patch

from meural_mcp.cli import build_parser, initialise_config_file, install_systemd_user_service, run_init_cloud


class CliTests(unittest.TestCase):
    def test_init_cloud_accepts_credentials_and_optional_api_token(self):
        args = build_parser().parse_args(
            [
                "init-cloud",
                "--username",
                "user@example.com",
                "--password",
                "password",
            ]
        )

        self.assertEqual(args.command, "init-cloud")
        self.assertEqual(args.username, "user@example.com")
        self.assertIsNone(args.api_token)

    def test_initialise_config_file_writes_token_not_credentials_and_backs_up_existing_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "config.json"
            config_path.write_text('{"old": true}')

            result = initialise_config_file(root, "shared")

            self.assertTrue(result["backup"])
            self.assertTrue(Path(result["backup"]).exists())
            written = config_path.read_text()
            self.assertIn('"api_token": "shared"', written)
            self.assertNotIn("user@example.com", written)
            self.assertNotIn("password", written)

    def test_run_init_cloud_generates_token_and_discovers_devices_without_storing_credentials(self):
        cloud = Mock()
        cloud.list_devices.return_value = {
            "data": [
                {"id": 1001, "alias": "Canvas One", "localIp": "192.0.2.10", "status": "online"},
                {"id": 1002, "alias": "Canvas Two", "localIp": "192.0.2.11", "status": "offline"},
            ]
        }
        cloud.list_galleries.return_value = {"data": []}
        cloud.create_gallery.return_value = {"data": {"id": 2001, "name": "MeuralMCP Blank Hold Landscape"}}
        cloud.list_gallery_items.return_value = {"data": []}
        cloud.upload_item_to_gallery.return_value = {"data": {"id": 3001}}
        cloud.list_device_galleries.return_value = {"data": []}

        with tempfile.TemporaryDirectory() as tmp, patch("meural_mcp.cli.secrets.token_urlsafe", return_value="generated-token"):
            result = run_init_cloud(Path(tmp), cloud)
            config = json.loads((Path(tmp) / "config.json").read_text())

        self.assertEqual(result["api_token"], "generated-token")
        self.assertEqual(config["api_token"], "generated-token")
        self.assertNotIn("cloud", config)
        self.assertEqual(config["devices"][0]["display_name"], "Canvas One")
        self.assertEqual(config["devices"][0]["cloud_id"], 1001)
        self.assertEqual(config["devices"][0]["local_ip"], "192.0.2.10")

    def test_run_init_cloud_uses_exact_number_of_cloud_devices(self):
        cloud = Mock()
        cloud.list_devices.return_value = {
            "data": [
                {"id": 1001, "alias": "Canvas One", "localIp": "192.0.2.10", "status": "online"},
                {"id": 1002, "alias": "Canvas Two", "localIp": "192.0.2.11", "status": "online"},
                {"id": 1003, "alias": "Canvas Three", "localIp": "192.0.2.12", "status": "online"},
            ]
        }
        cloud.list_galleries.return_value = {"data": [{"id": 2001, "name": "MeuralMCP Blank Hold Landscape"}]}
        cloud.list_gallery_items.return_value = {"data": [{"id": 3001}]}
        cloud.list_device_galleries.return_value = {"data": [{"id": 2001}]}

        with tempfile.TemporaryDirectory() as tmp:
            run_init_cloud(Path(tmp), cloud, api_token="token")
            config = json.loads((Path(tmp) / "config.json").read_text())

        self.assertEqual(len(config["devices"]), 3)
        self.assertEqual([device["name"] for device in config["devices"]], ["canvas-one", "canvas-two", "canvas-three"])

    def test_run_init_cloud_normalises_cloud_orientation_values(self):
        cloud = Mock()
        cloud.list_devices.return_value = {
            "data": [
                {"id": 1001, "alias": "Wide Canvas", "localIp": "192.0.2.10", "orientation": "horizontal"},
                {"id": 1002, "alias": "Tall Canvas", "localIp": "192.0.2.11", "orientation": "vertical"},
            ]
        }
        cloud.list_galleries.return_value = {"data": []}
        cloud.create_gallery.side_effect = [
            {"data": {"id": 2001, "name": "MeuralMCP Blank Hold Landscape"}},
            {"data": {"id": 2002, "name": "MeuralMCP Blank Hold Portrait"}},
        ]
        cloud.list_gallery_items.return_value = {"data": []}
        cloud.upload_item_to_gallery.side_effect = [{"data": {"id": 3001}}, {"data": {"id": 3002}}]
        cloud.list_device_galleries.return_value = {"data": []}

        with tempfile.TemporaryDirectory() as tmp:
            result = run_init_cloud(Path(tmp), cloud, api_token="token")
            config = json.loads((Path(tmp) / "config.json").read_text())

        self.assertEqual([device["orientation"] for device in config["devices"]], ["landscape", "portrait"])
        self.assertEqual(result["cloud"]["blank_galleries"]["galleries"]["landscape"]["assigned"], ["wide-canvas"])
        self.assertEqual(result["cloud"]["blank_galleries"]["galleries"]["portrait"]["assigned"], ["tall-canvas"])
        cloud.set_device_gallery.assert_any_call(1001, 2001)
        cloud.set_device_gallery.assert_any_call(1002, 2002)

    def test_run_init_cloud_reports_progress(self):
        cloud = Mock()
        cloud.list_devices.return_value = {"data": []}
        progress = Mock()

        with tempfile.TemporaryDirectory() as tmp:
            run_init_cloud(Path(tmp), cloud, api_token="token", progress=progress)

        messages = [call.args[0] for call in progress.call_args_list]
        self.assertIn("discovering devices from Meural cloud", messages)
        self.assertIn("discovered 0 device(s)", messages)
        self.assertIn("cloud init complete", messages)

    def test_systemd_unit_uses_meural_mcp_daemon_command(self):
        with tempfile.TemporaryDirectory() as tmp, patch("meural_mcp.cli.Path.home", return_value=Path(tmp)):
            unit = install_systemd_user_service(Path(tmp) / "state", executable="/usr/bin/meural-mcp --storage-dir /tmp/state daemon", run_systemctl=False)

            text = Path(unit).read_text()

        self.assertTrue(unit.endswith("meural-mcp.service"))
        self.assertIn("Description=MeuralMCP LAN preview manager", text)
        self.assertIn("ExecStart=/usr/bin/meural-mcp --storage-dir /tmp/state daemon", text)


if __name__ == "__main__":
    unittest.main()
