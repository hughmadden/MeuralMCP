"""Progress traverses each public surface without affecting mode/content ownership."""
import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from meural_mcp.api import create_app
from meural_mcp.api_client import RemoteApiClient
from meural_mcp.local import FrameLocalClient
from meural_mcp.manager import ManagerService
from meural_mcp.mcp_server import build_mcp_server, mcp_set_device_menu_progress
from meural_mcp.modes import progress_payload

DATA = {'epoch': 'session', 'mode_generation': 2, 'message': 'Preparing', 'percent': 25}
AUTH = {'Authorization': 'Bearer test-token'}


class MenuProgressTests(unittest.TestCase):
    def test_validation_preserves_percent_zero_and_rejects_malformed_or_unbounded_controls(self):
        self.assertEqual(progress_payload(dict(DATA, percent=0))['percent'], 0)
        for data in ({}, dict(DATA, percent=True), dict(DATA, percent=101),
                     dict(DATA, percent=float('nan')), dict(DATA, timeout_s=False),
                     dict(DATA, mode_generation=True), dict(DATA, message='x\ny'),
                     dict(DATA, clear=True), dict(DATA, unknown=1)):
            with self.assertRaises(ValueError):
                progress_payload(data)

    def test_native_and_remote_clients_forward_json_with_authentication(self):
        native = FrameLocalClient('192.0.2.10', token='frame-token')
        with patch('meural_mcp.local.requests.post') as post:
            post.return_value.status_code = 200
            native.set_menu_progress('reef', DATA)
            self.assertEqual(post.call_args.kwargs['json'], DATA)
            self.assertEqual(post.call_args.kwargs['params']['token'], 'frame-token')
        remote = RemoteApiClient('https://example.invalid', 'test-token')
        remote.request = Mock()
        remote.set_menu_progress('canvas-1', 'reef', DATA)
        remote.request.assert_called_once_with('POST', '/devices/canvas-1/modes/reef/progress', json=DATA)

    def test_manager_and_mcp_use_the_same_payload_without_selecting_modes(self):
        with tempfile.TemporaryDirectory() as root:
            native = Mock()
            manager = ManagerService(root=Path(root), config={'devices': [
                {'name': 'canvas-1', 'local_ip': '192.0.2.10', 'api_token': 'test-token'}]},
                local_client_factory=lambda host, timeout=5: native)
            manager.set_menu_progress('canvas-1', 'reef', DATA)
            native.set_menu_progress.assert_called_once_with('reef', DATA)
            native.select_mode.assert_not_called()
        manager = Mock()
        with patch('meural_mcp.mcp_server._remote_client', return_value=None), \
                patch('meural_mcp.mcp_server._service', return_value=manager):
            mcp_set_device_menu_progress('canvas-1', 'reef', DATA)
        manager.set_menu_progress.assert_called_once_with('canvas-1', identifier='reef', progress=DATA)
        names = {tool.name for tool in asyncio.run(build_mcp_server().list_tools())}
        self.assertIn('set_device_menu_progress', names)

    def test_rest_swagger_auth_strict_controls_and_native_conflict(self):
        manager = Mock()
        manager.set_menu_progress.return_value = {'status': 'pass'}
        with patch.dict(os.environ, {'MEURAL_MCP_API_TOKEN': 'test-token'}), \
                patch('meural_mcp.api.service', return_value=manager):
            client = TestClient(create_app())
            path = '/devices/canvas-1/modes/reef/progress'
            self.assertEqual(client.post(path, json=DATA).status_code, 401)
            for data in ({}, dict(DATA, percent=True), dict(DATA, mode_generation=True),
                         dict(DATA, clear=True), dict(DATA, unknown=1)):
                self.assertEqual(client.post(path, headers=AUTH, json=data).status_code, 422)
            self.assertEqual(client.post(path, headers=AUTH, json=DATA).status_code, 200)
            manager.set_menu_progress.assert_called_once_with('canvas-1', 'reef', DATA)
            clear = {'epoch': 'session', 'mode_generation': 2, 'clear': True}
            self.assertEqual(client.post(path, headers=AUTH, json=clear).status_code, 200)
            manager.set_menu_progress.return_value = {'status': 'failed', 'http_status': 409}
            self.assertEqual(client.post(path, headers=AUTH, json=clear).status_code, 409)
            spec = client.get('/openapi.json').json()
            self.assertIn('/devices/{name}/modes/{mode_id}/progress', spec['paths'])


if __name__ == '__main__':
    unittest.main()
