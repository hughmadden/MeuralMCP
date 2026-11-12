"""Prepared-media contracts across binary REST, local clients and both MCP modes."""

import asyncio
import base64
import errno
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests
from fastapi.testclient import TestClient

from meural_mcp.api import TemporaryMediaResponse, create_app
from meural_mcp.api_client import RemoteApiClient
from meural_mcp.local import FrameLocalClient, FrameLocalError
from meural_mcp.manager import ManagerService
from meural_mcp import mcp_server, video
from meural_mcp.video import (HOST_SPOOL_RESERVE_BYTES, HostSpool, MCP_VIDEO_MAX_BYTES,
                              VideoTransferError, copy_video_download, playback_payload,
                              spool_available_bytes)

NAME = "canvas-1"
GALLERY = "a" * 32
FILE = "b" * 32
AUTH = {"Authorization": "Bearer test-service-token"}
BASE = f"/devices/{NAME}/video"
MEDIA = b"prepared opaque media"
FILE_INFO = {"id": FILE, "gallery_id": GALLERY, "kind": "video", "title": "Clip",
             "filename": "clip.mp4", "bytes": len(MEDIA), "metadata": {}, "created_at": 1}
VIDEO_TOOLS = {
    "get_device_video", "get_device_video_storage", "list_device_video_galleries",
    "create_device_video_gallery", "get_device_video_gallery", "delete_device_video_gallery",
    "list_device_video_files", "get_device_video_file", "delete_device_video_file",
    "upload_device_video_file", "download_device_video_file", "play_device_video", "stop_device_video",
}


class VideoManagerTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.frame = Mock()
        self.config = {"devices": [{"name": NAME, "local_ip": "192.0.2.10",
                                    "api_token": "test-frame-token", "orientation": "landscape"}]}
        self.manager = ManagerService(root=self.root, config=self.config,
                                      local_client_factory=lambda ip, timeout=5: self.frame)
        for method in ("video_status", "video_storage", "list_video_galleries", "create_video_gallery",
                       "get_video_gallery", "delete_video_gallery", "list_video_files",
                       "get_video_file", "delete_video_file", "upload_video_file", "play_video", "stop_video"):
            getattr(self.frame, method).return_value = {"is_ok": True}

    def tearDown(self):
        self.folder.cleanup()

    def test_capabilities_inventory_and_crud_preserve_envelopes(self):
        self.frame.video_status.return_value = {"is_ok": True, "capabilities": {"video": True},
                                                "layers": {"fullscreen": {}, "overlay": {}}}
        result = self.manager.video_status(NAME)
        self.assertEqual(result["status"], "pass")
        self.assertIn("overlay", result["layers"])
        for method, args in (("video_storage", ()), ("list_video_galleries", ()),
                             ("create_video_gallery", ("Clips",)), ("get_video_gallery", (GALLERY,)),
                             ("delete_video_gallery", (GALLERY,)), ("list_video_files", ()),
                             ("get_video_file", (FILE,)), ("delete_video_file", (FILE,))):
            with self.subTest(method=method):
                self.assertEqual(getattr(self.manager, method)(NAME, *args)["status"], "pass")
                getattr(self.frame, method).assert_called_with(*args)

    def test_repeat_counts_and_sources_are_not_mapped_to_booleans(self):
        for repeat in (1, 0, 4):
            for source in ({"file_id": FILE}, {"gallery_id": GALLERY}, {"url": "https://media.example.test/live"}):
                with self.subTest(repeat=repeat, source=source):
                    self.manager.play_video(NAME, **source, repeat=repeat, presentation="overlay")
                    self.frame.play_video.assert_called_with(**source, repeat=repeat, presentation="overlay")
        self.manager.play_video(NAME, file_id=FILE)
        self.frame.play_video.assert_called_with(file_id=FILE, repeat=1, presentation="auto")

    def test_stream_protocol_validation_matches_native_layers(self):
        for scheme in ("http", "https", "rtsp", "rtsps"):
            url = scheme + "://user:secret@192.0.2.1/live"
            self.manager.play_video(NAME, url=url, presentation="fullscreen")
            self.frame.play_video.assert_called_with(url=url, repeat=1, presentation="fullscreen")
        for url in ("http://192.0.2.1:bad/live", "http://192.0.2.1:0/live", "http://192.0.2.1/\nprivate"):
            with self.assertRaises(ValueError):
                self.manager.play_video(NAME, url=url)
        for scheme in ("rtsp", "rtsps"):
            with self.assertRaises(ValueError):
                self.manager.play_video(NAME, url=scheme+"://192.0.2.1/live", presentation="overlay")

    def test_invalid_play_does_not_reach_frame(self):
        invalid = ({}, {"file_id": FILE, "gallery_id": GALLERY}, {"file_id": ""},
                   {"url": "file:///tmp/video"}, {"file_id": FILE, "repeat": -1},
                   {"file_id": FILE, "repeat": True}, {"file_id": FILE, "repeat": 1.2},
                   {"file_id": FILE, "presentation": "all"})
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.manager.play_video(NAME, **payload)
        self.frame.play_video.assert_not_called()

    def test_stop_layer_and_all(self):
        self.manager.stop_video(NAME)
        self.frame.stop_video.assert_called_with("all")
        for presentation in ("fullscreen", "overlay"):
            self.manager.stop_video(NAME, presentation)
            self.frame.stop_video.assert_called_with(presentation)
        with self.assertRaises(ValueError):
            self.manager.stop_video(NAME, "auto")

    def test_opaque_overlay_upload_and_repeat_forwarding(self):
        source = self.root / "prepared.alpha"
        source.write_bytes(MEDIA)
        self.manager.upload_video_file(NAME, GALLERY, source, filename="clip.alpha", kind="overlay",
                                       play=True, repeat=0, presentation="overlay")
        self.frame.upload_video_file.assert_called_once_with(
            GALLERY, source, filename="clip.alpha", kind="overlay", title=None,
            play=True, repeat=0, presentation="overlay")
        self.assertEqual(source.read_bytes(), MEDIA)

    def test_http_errors_preserved_without_implicit_stop(self):
        for status, error in ((400, "invalid"), (403, "forbidden"), (404, "missing"),
                              (409, "in_use"), (507, "insufficient_space")):
            with self.subTest(status=status):
                self.frame.delete_video_file.side_effect = FrameLocalError(
                    status, "http://192.0.2.10:8099/local/video/files/id", json.dumps({"is_ok": False, "error": error}))
                result = self.manager.delete_video_file(NAME, FILE)
                self.assertEqual(result["http_status"], status)
                self.assertEqual(result["error"], error)
                self.assertFalse(result["is_ok"])
        self.frame.stop_video.assert_not_called()

    def test_frame_error_false_and_network_error_are_failures(self):
        self.frame.video_status.return_value = {"is_ok": False, "error": "not_supported"}
        self.assertEqual(self.manager.video_status(NAME)["status"], "failed")
        self.frame.video_status.side_effect = requests.Timeout("secret-bearing-url")
        result = self.manager.video_status(NAME)
        self.assertEqual(result["http_status"], 502)
        self.assertNotIn("secret-bearing-url", str(result))

    def test_token_required_and_route_traversal_rejected(self):
        self.config["devices"][0].pop("api_token")
        self.assertEqual(self.manager.video_status(NAME)["reason"], "on_device_api_required")
        self.frame.video_status.assert_not_called()
        with self.assertRaises(ValueError):
            self.manager.get_video_file(NAME, "../settings")


class VideoLocalClientTests(unittest.TestCase):
    def setUp(self):
        self.client = FrameLocalClient("192.0.2.10", "test-frame-token")
        self.response = Mock(status_code=200, headers={})
        self.response.json.return_value = {"is_ok": True}

    def test_reads_and_delete_auth(self):
        with patch("meural_mcp.local.requests.get", return_value=self.response) as get:
            for method, suffix, args in (("video_status", "", ()), ("video_storage", "/storage", ()),
                                        ("list_video_galleries", "/galleries", ()),
                                        ("get_video_gallery", f"/galleries/{GALLERY}", (GALLERY,)),
                                        ("list_video_files", "/files", ()),
                                        ("get_video_file", f"/files/{FILE}", (FILE,))):
                getattr(self.client, method)(*args)
                self.assertEqual(get.call_args.args[0], self.client.base_url + "/local/video" + suffix)
                self.assertEqual(get.call_args.kwargs["params"], {"token": "test-frame-token"})
        with patch("meural_mcp.local.requests.delete", return_value=self.response) as delete:
            self.client.delete_video_file(FILE)
            self.assertTrue(delete.call_args.args[0].endswith("/files/" + FILE))
            self.assertEqual(delete.call_args.kwargs["params"], {"token": "test-frame-token"})
            self.client.delete_video_gallery(GALLERY)
            self.assertTrue(delete.call_args.args[0].endswith("/galleries/" + GALLERY))

    def test_create_play_stop_use_form_primitives(self):
        with patch("meural_mcp.local.requests.post", return_value=self.response) as post:
            self.client.create_video_gallery("Clips")
            self.assertEqual(post.call_args.kwargs["data"], {"name": "Clips"})
            self.client.play_video(file_id=FILE, repeat=0, presentation="fullscreen")
            self.assertEqual(post.call_args.kwargs["data"], {"file_id": FILE, "repeat": 0, "presentation": "fullscreen"})
            self.client.stop_video("overlay")
            self.assertEqual(post.call_args.kwargs["data"], {"presentation": "overlay"})

    def test_upload_streams_file_and_preserves_options(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "clip.mp4"
            path.write_bytes(MEDIA)
            def upload(url, **kw):
                self.assertEqual(kw["data"].read(), MEDIA)
                self.assertEqual(kw["params"]["play"], "false")
                self.assertEqual(kw["params"]["repeat"], 4)
                self.assertEqual(kw["params"]["token"], "test-frame-token")
                self.assertNotIn("files", kw)
                return self.response
            with patch("meural_mcp.local.requests.post", side_effect=upload):
                self.client.upload_video_file(GALLERY, path, play=False, repeat=4, kind="video")

    def test_download_stream_and_bound_clean_up(self):
        self.response.__enter__ = Mock(return_value=self.response)
        self.response.__exit__ = Mock(return_value=False)
        self.response.iter_content.return_value = iter([MEDIA[:5], MEDIA[5:]])
        with tempfile.TemporaryDirectory() as folder, patch("meural_mcp.local.requests.get", return_value=self.response):
            path = Path(folder) / "clip"
            result = self.client.download_video_file(FILE, path, max_bytes=len(MEDIA))
            self.assertEqual(result["size_bytes"], len(MEDIA))
            self.assertEqual(path.read_bytes(), MEDIA)
            self.response.iter_content.return_value = iter([MEDIA])
            with self.assertRaises(ValueError):
                self.client.download_video_file(FILE, path, max_bytes=1)
            self.assertFalse(path.exists())

    def test_error_redacts_token(self):
        self.response.status_code = 409
        self.response.url = "http://192.0.2.10/?token=test-frame-token"
        self.response.text = '{"is_ok":false,"error":"in_use"}'
        with patch("meural_mcp.local.requests.delete", return_value=self.response), self.assertRaises(FrameLocalError) as error:
            self.client.delete_video_file(FILE)
        self.assertEqual(error.exception.status, 409)
        self.assertNotIn("test-frame-token", str(error.exception))


class VideoApiTests(VideoManagerTests):
    def setUp(self):
        super().setUp()
        self.environment = patch.dict(os.environ, {"MEURAL_MCP_API_TOKEN": "test-service-token"})
        self.environment.start()
        self.service_patch = patch("meural_mcp.api.service", return_value=self.manager)
        self.service_patch.start()
        self.client = TestClient(create_app())
        self.frame.video_storage.return_value = {"is_ok": True, "storage": {"available_upload_bytes": 1000}}
        self.frame.get_video_file.return_value = {"is_ok": True, "file": FILE_INFO}

    def tearDown(self):
        self.service_patch.stop()
        self.environment.stop()
        super().tearDown()

    def test_all_video_routes_require_authentication(self):
        for path, methods in self.client.get("/openapi.json").json()["paths"].items():
            if "/video" not in path:
                continue
            url = path.format(name=NAME, gallery_id=GALLERY, file_id=FILE)
            for method in methods:
                if method in {"get", "post", "delete"}:
                    with self.subTest(method=method, path=path):
                        self.assertEqual(self.client.request(method, url).status_code, 401)

    def test_typed_play_count_and_stop_layer_defaults(self):
        for repeat in (0, 1, 3):
            response = self.client.post(BASE, headers=AUTH, json={"gallery_id": GALLERY, "repeat": repeat, "presentation": "fullscreen"})
            self.assertEqual(response.status_code, 200)
            self.frame.play_video.assert_called_with(gallery_id=GALLERY, repeat=repeat, presentation="fullscreen")
        self.client.post(BASE, headers=AUTH, json={"file_id": FILE})
        self.frame.play_video.assert_called_with(file_id=FILE, repeat=1, presentation="auto")
        self.client.post(BASE + "/stop", headers=AUTH)
        self.frame.stop_video.assert_called_with("all")
        self.client.post(BASE + "/stop", headers=AUTH, json={"presentation": "overlay"})
        self.frame.stop_video.assert_called_with("overlay")
        for payload in ({"file_id": FILE, "repeat": True}, {"file_id": FILE, "repeat": -1},
                        {"file_id": FILE, "repeat": 1.5}, {}, {"file_id": FILE, "url": "https://media.example.test/clip"}):
            with self.subTest(payload=payload):
                self.assertEqual(self.client.post(BASE, headers=AUTH, json=payload).status_code, 422)

    def test_binary_upload_spools_and_cleans_prepared_bytes(self):
        seen = []
        def upload(gallery_id, path, **options):
            seen.append(path)
            self.assertEqual(path.read_bytes(), MEDIA)
            self.assertEqual(gallery_id, GALLERY)
            self.assertEqual(options["kind"], "overlay")
            self.assertEqual(options["repeat"], 4)
            self.assertFalse(options["play"])
            return {"is_ok": True, "file": {**FILE_INFO, "kind": "overlay"}}
        self.frame.upload_video_file.side_effect = upload
        response = self.client.post(BASE + f"/galleries/{GALLERY}/files?filename=clip.alpha&kind=overlay&repeat=4",
                                    headers={**AUTH, "Content-Type": "application/octet-stream"}, content=MEDIA)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["file"]["kind"], "overlay")
        self.assertEqual(len(seen), 1)
        self.assertFalse(seen[0].exists())

    def test_upload_requires_length_and_checks_storage_before_forwarding(self):
        path = BASE + f"/galleries/{GALLERY}/files"
        self.assertEqual(self.client.post(path, headers=AUTH, content=iter([MEDIA])).status_code, 411)
        self.assertEqual(self.client.post(path, headers=AUTH, content=b"").status_code, 400)
        self.frame.video_storage.return_value["storage"]["available_upload_bytes"] = 1
        self.assertEqual(self.client.post(path, headers=AUTH, content=MEDIA).status_code, 507)
        self.frame.upload_video_file.assert_not_called()
        self.frame.video_storage.return_value["storage"]["available_upload_bytes"] = 1000
        with patch("meural_mcp.api.spool_available_bytes", return_value=1):
            self.assertEqual(self.client.post(path, headers=AUTH, content=MEDIA).status_code, 507)
        self.frame.upload_video_file.assert_not_called()
        self.assertEqual(self.client.post(path, headers={**AUTH, "Content-Length": "1"}, content=MEDIA).status_code, 400)

    def test_upload_error_and_cleanup(self):
        seen = []
        def upload(gallery_id, path, **options):
            seen.append(path)
            raise FrameLocalError(507, "http://192.0.2.10", '{"is_ok":false,"error":"insufficient_space"}')
        self.frame.upload_video_file.side_effect = upload
        response = self.client.post(BASE + f"/galleries/{GALLERY}/files", headers=AUTH, content=MEDIA)
        self.assertEqual(response.status_code, 507)
        self.assertEqual(response.json()["error"], "insufficient_space")
        self.assertFalse(seen[0].exists())

    def test_download_binary_and_cleanup_after_response(self):
        seen = []
        def download(file_id, destination, max_bytes=None):
            seen.append(destination)
            destination.write_bytes(MEDIA)
            return {"is_ok": True, "size_bytes": len(MEDIA)}
        self.frame.download_video_file.side_effect = download
        response = self.client.get(BASE + f"/files/{FILE}/data", headers=AUTH)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, MEDIA)
        self.assertEqual(response.headers["content-type"], "application/octet-stream")
        self.assertFalse(seen[0].exists())

    def test_missing_file_download_and_in_use_delete_keep_http_status(self):
        self.frame.get_video_file.side_effect = FrameLocalError(404, "http://192.0.2.10", '{"is_ok":false,"error":"missing"}')
        self.assertEqual(self.client.get(BASE + f"/files/{FILE}/data", headers=AUTH).status_code, 404)
        self.frame.delete_video_gallery.side_effect = FrameLocalError(409, "http://192.0.2.10", '{"is_ok":false,"error":"in_use"}')
        response = self.client.delete(BASE + f"/galleries/{GALLERY}", headers=AUTH)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"], "in_use")
        self.frame.stop_video.assert_not_called()

    def test_actual_spool_write_fsync_and_close_failures_are_safe_507(self):
        real_create, real_write = tempfile.mkstemp, os.write
        real_sync, real_close = os.fsync, os.close
        for operation in ("write", "fsync", "close"):
            for number in (errno.ENOSPC, errno.EDQUOT):
                with self.subTest(operation=operation, errno=number):
                    paths, descriptors = [], set()
                    def create(**kwargs):
                        fd, name = real_create(**kwargs)
                        paths.append(Path(name))
                        descriptors.add(fd)
                        return fd, name
                    def write(fd, data):
                        if fd in descriptors and operation == "write":
                            raise OSError(number, "private-path-and-token")
                        return real_write(fd, data)
                    def sync(fd):
                        if fd in descriptors and operation == "fsync":
                            raise OSError(number, "private-path-and-token")
                        return real_sync(fd)
                    def close(fd):
                        owned = fd in descriptors
                        descriptors.discard(fd)
                        real_close(fd)
                        if owned and operation == "close":
                            raise OSError(number, "private-path-and-token")
                    with patch("meural_mcp.video.tempfile.mkstemp", side_effect=create), patch(
                            "meural_mcp.video.os.write", side_effect=write), patch(
                            "meural_mcp.video.os.fsync", side_effect=sync), patch(
                            "meural_mcp.video.os.close", side_effect=close):
                        result = self.client.post(BASE + f"/galleries/{GALLERY}/files", headers=AUTH, content=MEDIA)
                    self.assertEqual(result.status_code, 507)
                    self.assertFalse(result.json()["is_ok"])
                    self.assertNotIn("private-path-and-token", result.text)
                    self.assertTrue(paths)
                    self.assertTrue(all(not path.exists() for path in paths))
                    self.frame.upload_video_file.assert_not_called()

    def test_upload_disconnect_releases_spool_and_reservation(self):
        from starlette.requests import ClientDisconnect
        paths = []
        real_create = tempfile.mkstemp
        def create(**kwargs):
            fd, name = real_create(**kwargs)
            paths.append(Path(name))
            return fd, name
        class DisconnectedRequest:
            headers = {"content-length": "100"}
            async def stream(self):
                yield b"partial"
                raise ClientDisconnect()
        app = create_app()
        endpoint = next(route.endpoint for route in app.routes
                        if getattr(route, "path", None) == "/devices/{name}/video/galleries/{gallery_id}/files")
        with patch("meural_mcp.video.tempfile.mkstemp", side_effect=create):
            with self.assertRaises(ClientDisconnect):
                asyncio.run(endpoint(NAME, GALLERY, DisconnectedRequest()))
        self.assertTrue(paths)
        self.assertTrue(all(not path.exists() for path in paths))
        self.frame.upload_video_file.assert_not_called()
        self.assertEqual(video._SPOOL_RESERVED, {})

    def test_openapi_exposes_source_count_and_presentation(self):
        schema = self.client.get("/openapi.json").json()
        play = schema["components"]["schemas"]["VideoPlay"]["properties"]
        self.assertEqual(play["repeat"]["default"], 1)
        self.assertEqual(play["repeat"]["minimum"], 0)
        self.assertEqual(play["presentation"]["enum"], ["auto", "fullscreen", "overlay"])
        upload = schema["paths"]["/devices/{name}/video/galleries/{gallery_id}/files"]["post"]
        self.assertIn("application/octet-stream", upload["requestBody"]["content"])


class VideoMcpTests(unittest.TestCase):
    def setUp(self):
        self.service = Mock()
        self.service.play_video.return_value = {"status": "pass"}
        self.service.upload_video_file.return_value = {"status": "pass", "file": FILE_INFO}
        self.service.video_status.return_value = {"status": "pass", "layers": {"fullscreen": {}, "overlay": {}}}
        self.service_patch = patch("meural_mcp.mcp_server._service", return_value=self.service)
        self.remote_patch = patch("meural_mcp.mcp_server._remote_client", return_value=None)
        self.service_patch.start()
        self.remote_patch.start()

    def tearDown(self):
        self.service_patch.stop()
        self.remote_patch.stop()

    def test_all_25_existing_tools_plus_13_video_tools(self):
        tools = asyncio.run(mcp_server.build_mcp_server().list_tools())
        names = {tool.name for tool in tools}
        self.assertEqual(len(names), 38)
        self.assertTrue(VIDEO_TOOLS.issubset(names))
        self.assertTrue({"get_device_status", "set_device_image_data", "get_device_settings", "run_device_action"}.issubset(names))

    def test_status_and_play_forward_to_manager(self):
        self.assertIn("overlay", mcp_server.mcp_get_device_video(NAME)["layers"])
        for repeat in (0, 1, 4):
            mcp_server.mcp_play_device_video(NAME, gallery_id=GALLERY, repeat=repeat, presentation="overlay")
            self.service.play_video.assert_called_with(NAME, gallery_id=GALLERY, repeat=repeat, presentation="overlay")
        self.assertEqual(mcp_server.mcp_play_device_video(NAME, file_id=FILE, repeat=True)["status"], "failed")

    def test_upload_bound_invalid_and_cleanup(self):
        seen = []
        def upload(name, gallery_id, source, **options):
            seen.append(source)
            self.assertEqual(source.read_bytes(), MEDIA)
            self.assertEqual(options["kind"], "overlay")
            return {"status": "pass", "file": FILE_INFO}
        self.service.upload_video_file.side_effect = upload
        result = mcp_server.mcp_upload_device_video_file(NAME, GALLERY, base64.b64encode(MEDIA).decode(), kind="overlay")
        self.assertEqual(result["status"], "pass")
        self.assertFalse(seen[0].exists())
        self.assertEqual(mcp_server.mcp_upload_device_video_file(NAME, GALLERY, "!")["reason"], "video_base64_invalid")
        self.assertEqual(mcp_server.mcp_upload_device_video_file(NAME, GALLERY, "")["reason"], "video_size_invalid")
        with patch("meural_mcp.mcp_server.MCP_VIDEO_MAX_BYTES", 2):
            self.assertEqual(mcp_server.mcp_upload_device_video_file(NAME, GALLERY, "aaaaa")["reason"], "video_too_large")
        self.assertEqual(len(seen), 1)

    def test_base64_upload_full_preflight_does_not_create_files(self):
        full = Mock(free=HOST_SPOOL_RESERVE_BYTES)
        with patch("meural_mcp.video.shutil.disk_usage", return_value=full), patch(
                "meural_mcp.video.tempfile.mkstemp") as create:
            result = mcp_server.mcp_upload_device_video_file(
                NAME, GALLERY, base64.b64encode(MEDIA).decode())
        self.assertEqual(result["http_status"], 507)
        self.assertFalse(result["is_ok"])
        create.assert_not_called()
        self.service.upload_video_file.assert_not_called()

    def test_base64_upload_disk_write_failure_is_507_and_cleans(self):
        paths = []
        real_create = tempfile.mkstemp
        def create(**kwargs):
            fd, name = real_create(**kwargs)
            paths.append(Path(name))
            return fd, name
        with patch("meural_mcp.video.tempfile.mkstemp", side_effect=create), patch(
                "meural_mcp.video.os.write", side_effect=OSError(errno.ENOSPC, "private-storage-path")):
            result = mcp_server.mcp_upload_device_video_file(
                NAME, GALLERY, base64.b64encode(MEDIA).decode())
        self.assertEqual(result["http_status"], 507)
        self.assertNotIn("private-storage-path", str(result))
        self.assertTrue(all(not path.exists() for path in paths))
        self.service.upload_video_file.assert_not_called()
        self.assertEqual(video._SPOOL_RESERVED, {})

    def test_remote_download_transfer_errors_preserve_507(self):
        remote = Mock()
        remote.download_video_file.side_effect = VideoTransferError("host temporary storage operation failed", 507)
        with patch("meural_mcp.mcp_server._remote_client", return_value=remote):
            result = mcp_server.mcp_download_device_video_file(NAME, FILE)
        self.assertEqual(result["http_status"], 507)
        self.assertFalse(remote.download_video_file.call_args.kwargs["destination"].exists())

    def test_bounded_download_encodes_and_cleans(self):
        seen = []
        def download(name, file_id, destination, max_bytes):
            seen.append(destination)
            self.assertEqual(max_bytes, MCP_VIDEO_MAX_BYTES)
            destination.write_bytes(MEDIA)
            return {"status": "pass", "size_bytes": len(MEDIA)}
        self.service.download_video_file.side_effect = download
        result = mcp_server.mcp_download_device_video_file(NAME, FILE)
        self.assertEqual(base64.b64decode(result["video_base64"]), MEDIA)
        self.assertFalse(seen[0].exists())
        self.service.download_video_file.side_effect = ValueError("too large")
        self.assertEqual(mcp_server.mcp_download_device_video_file(NAME, FILE)["status"], "failed")

    def test_remote_stdio_upload_play_stop_and_errors(self):
        remote = Mock()
        remote.upload_video_file.return_value = {"status": "pass", "file": FILE_INFO}
        remote.delete_video_file.return_value = {"status": "failed", "is_ok": False, "error": "in_use", "http_status": 409}
        with patch("meural_mcp.mcp_server._remote_client", return_value=remote):
            mcp_server.mcp_upload_device_video_file(NAME, GALLERY, base64.b64encode(MEDIA).decode(), repeat=0, play=True)
            self.assertEqual(remote.upload_video_file.call_args.kwargs["repeat"], 0)
            self.assertFalse(remote.upload_video_file.call_args.kwargs["source"].exists())
            mcp_server.mcp_play_device_video(NAME, file_id=FILE)
            remote.play_video.assert_called_with(NAME, file_id=FILE, repeat=1, presentation="auto")
            mcp_server.mcp_stop_device_video(NAME, "fullscreen")
            remote.stop_video.assert_called_with(NAME, presentation="fullscreen")
            self.assertEqual(mcp_server.mcp_delete_device_video_file(NAME, FILE)["http_status"], 409)
            remote.stop_video.assert_called_once()

    def test_hosted_mcp_auth_and_tool_inventory(self):
        with patch.dict(os.environ, {"MEURAL_MCP_API_TOKEN": "test-service-token"}), TestClient(create_app()) as client:
            headers = {**AUTH, "Accept": "application/json, text/event-stream"}
            payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
            self.assertEqual(client.post("/mcp/", json=payload).status_code, 401)
            response = client.post("/mcp/", headers=headers, json=payload)
            self.assertEqual(response.status_code, 200)
            text = response.text
            if text.startswith("event:"):
                text = next(line[6:] for line in text.splitlines() if line.startswith("data: "))
            tools = json.loads(text)["result"]["tools"]
            self.assertEqual(len(tools), 38)
            self.assertTrue(VIDEO_TOOLS.issubset({tool["name"] for tool in tools}))


class VideoTransferCleanupTests(unittest.TestCase):
    def test_response_failure_removes_spool(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "prepared-media"
            path.write_bytes(MEDIA)
            response = TemporaryMediaResponse(path)
            async def broken_response(*args):
                raise OSError("client disconnected")
            with patch("fastapi.responses.FileResponse.__call__", side_effect=broken_response):
                with self.assertRaises(OSError):
                    asyncio.run(response({}, None, None))
            self.assertFalse(path.exists())

    def test_download_rechecks_host_space_and_cleans_partial_file(self):
        response = Mock(headers={})
        response.iter_content.return_value = iter([MEDIA[:5], MEDIA[5:]])
        plenty = Mock(free=HOST_SPOOL_RESERVE_BYTES + 100000)
        full = Mock(free=HOST_SPOOL_RESERVE_BYTES)
        samples = [0]
        def disk(location):
            samples[0] += 1
            return plenty if samples[0] <= 4 else full
        with tempfile.TemporaryDirectory() as folder, patch(
                "meural_mcp.video.shutil.disk_usage", side_effect=disk):
            path = Path(folder) / "prepared-media"
            with self.assertRaises(VideoTransferError) as error:
                copy_video_download(response, path, None)
            self.assertEqual(error.exception.http_status, 507)
            self.assertFalse(path.exists())

    def test_documented_video_tools_and_packaged_mirrors(self):
        root = Path(__file__).parents[1]
        agents = (root / "docs/agents.md").read_text()
        llms = (root / "llms.txt").read_text()
        for tool in VIDEO_TOOLS:
            self.assertIn(tool, agents)
            self.assertIn(tool, llms)
        self.assertNotIn("8099", agents)
        self.assertNotIn("certificate", agents)
        self.assertNotIn("per-frame token", agents)
        self.assertEqual(agents, (root / "src/meural_mcp/data/agents.md").read_text())
        self.assertEqual(llms, (root / "src/meural_mcp/data/llms.txt").read_text())


class HostSpoolTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        info = os.statvfs(self.root)
        self.block = info.f_frsize or info.f_bsize or 4096
        self.free_samples = []

    def disk(self, location):
        used = sum(((path.stat().st_size + self.block - 1) // self.block) * self.block
                   for path in self.root.iterdir() if path.is_file())
        free = HOST_SPOOL_RESERVE_BYTES + 3 * self.block - used
        self.free_samples.append(free)
        return Mock(free=free)

    def test_overlapping_zero_length_spools_can_close_in_either_order(self):
        for reverse in (False, True):
            with self.subTest(reverse=reverse):
                first = HostSpool(0, destination=self.root / "zero-first")
                second = HostSpool(0, destination=self.root / "zero-second")
                first.__enter__()
                second.__enter__()
                spools = (second, first) if reverse else (first, second)
                try:
                    spools[0].close()
                    spools[1].close()
                finally:
                    for spool in spools:
                        spool.close(sync=False)
                self.assertEqual(video._SPOOL_RESERVED, {})

    def test_unknown_length_spool_survives_other_transfer_releasing_device_key(self):
        with HostSpool(destination=self.root / "unknown-overlap") as unknown:
            with HostSpool(3, destination=self.root / "known-overlap") as known:
                known.write(b"abc")
            # The known transfer has no remaining promise. The unknown transfer
            # must still write and close safely without an accounting entry.
            unknown.write(b"def")
        self.assertEqual((self.root / "unknown-overlap").read_bytes(), b"def")
        self.assertEqual(video._SPOOL_RESERVED, {})

    def test_remaining_reservations_protect_floor_during_concurrent_transfers(self):
        entered, proceed = threading.Event(), threading.Event()
        errors = []
        destination = self.root / "known"
        def known_upload():
            try:
                with HostSpool(2 * self.block, destination=destination) as media:
                    media.write(b"a" * self.block)
                    entered.set()
                    if not proceed.wait(3):
                        raise RuntimeError("synchronization timeout")
                    media.write(b"b" * self.block)
            except BaseException as error:
                errors.append(error)
        with patch("meural_mcp.video.shutil.disk_usage", side_effect=self.disk):
            thread = threading.Thread(target=known_upload)
            thread.start()
            try:
                self.assertTrue(entered.wait(3))
                # Written bytes moved from promises to actual disk use, so the
                # second admission sees one block, rather than double charging.
                self.assertEqual(spool_available_bytes(self.root), self.block)
                with self.assertRaises(VideoTransferError) as failure:
                    with HostSpool(2 * self.block, destination=self.root / "rejected"):
                        self.fail("concurrent upload was incorrectly admitted")
                self.assertEqual(failure.exception.http_status, 507)
                self.assertFalse((self.root / "rejected").exists())
                with HostSpool(destination=self.root / "unknown") as unknown:
                    with self.assertRaises(VideoTransferError):
                        unknown.write(b"c" * (2 * self.block))
                    unknown.write(b"d" * self.block)
                self.assertEqual(spool_available_bytes(self.root), 0)
            finally:
                proceed.set()
                thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(all(free >= HOST_SPOOL_RESERVE_BYTES for free in self.free_samples))
        self.assertEqual(video._SPOOL_RESERVED, {})
        self.assertEqual(destination.read_bytes(), b"a" * self.block + b"b" * self.block)

    def test_failed_or_incomplete_transfer_releases_unwritten_reservation(self):
        with patch("meural_mcp.video.shutil.disk_usage", side_effect=self.disk):
            for write_bytes in (0, self.block):
                with self.assertRaises(RuntimeError):
                    with HostSpool(2 * self.block, destination=self.root / "partial") as media:
                        if write_bytes:
                            media.write(b"a" * write_bytes)
                        raise RuntimeError("reader disconnected")
                self.assertFalse((self.root / "partial").exists())
                self.assertEqual(spool_available_bytes(self.root), 3 * self.block)
        self.assertEqual(video._SPOOL_RESERVED, {})

    def test_download_write_and_close_failures_cleanup_and_release(self):
        response = Mock(headers={"Content-Length": str(self.block)})
        real_write, real_close = os.write, os.close
        for operation in ("write", "close"):
            with self.subTest(operation=operation):
                response.iter_content.return_value = iter([b"a" * self.block])
                path = self.root / "download"
                def write(fd, data):
                    if operation == "write":
                        raise OSError(errno.ENOSPC, "private-source-token")
                    return real_write(fd, data)
                def close(fd):
                    real_close(fd)
                    if operation == "close":
                        raise OSError(errno.EDQUOT, "private-source-token")
                with patch("meural_mcp.video.shutil.disk_usage", side_effect=self.disk), patch(
                        "meural_mcp.video.os.write", side_effect=write), patch(
                        "meural_mcp.video.os.close", side_effect=close):
                    with self.assertRaises(VideoTransferError) as failure:
                        copy_video_download(response, path, self.block)
                self.assertEqual(failure.exception.http_status, 507)
                self.assertNotIn("private-source-token", str(failure.exception))
                self.assertFalse(path.exists())
                self.assertEqual(video._SPOOL_RESERVED, {})

    def test_allocation_settled_at_close_cannot_leave_reserve_consumed(self):
        settled = [False]
        real_sync = os.fsync
        path = self.root / "deferred-allocation"
        def sync(fd):
            real_sync(fd)
            settled[0] = True
        def disk(location):
            free = HOST_SPOOL_RESERVE_BYTES - 1 if settled[0] else HOST_SPOOL_RESERVE_BYTES + 100000
            return Mock(free=free)
        with patch("meural_mcp.video.os.fsync", side_effect=sync), patch(
                "meural_mcp.video.shutil.disk_usage", side_effect=disk):
            with self.assertRaises(VideoTransferError) as failure:
                with HostSpool(3, destination=path) as media:
                    media.write(b"abc")
        self.assertEqual(failure.exception.http_status, 507)
        self.assertFalse(path.exists())
        self.assertEqual(video._SPOOL_RESERVED, {})

    def test_short_writes_and_large_input_are_bounded(self):
        payload = b"a" * (video.TRANSFER_CHUNK_BYTES + 12345)
        path = self.root / "bounded"
        real_write = os.write
        sizes = []
        def write(fd, data):
            sizes.append(len(data))
            return real_write(fd, data[:8192])
        with patch("meural_mcp.video.os.write", side_effect=write):
            with HostSpool(len(payload), destination=path) as media:
                media.write(payload)
        self.assertEqual(path.read_bytes(), payload)
        self.assertTrue(all(0 < size <= video.TRANSFER_CHUNK_BYTES for size in sizes))
        self.assertEqual(video._SPOOL_RESERVED, {})


class VideoRemoteClientTests(unittest.TestCase):
    def setUp(self):
        self.client = RemoteApiClient("https://meural-mcp.example.test", "test-service-token")

    def test_binary_upload_streams_and_play_stop_payloads(self):
        response = Mock(status_code=200)
        response.json.return_value = {"status": "pass"}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "clip.mp4"
            path.write_bytes(MEDIA)
            def send(method, url, **kw):
                if "data" in kw:
                    self.assertEqual(kw["data"].read(), MEDIA)
                    self.assertEqual(kw["params"]["repeat"], 3)
                    self.assertEqual(kw["headers"]["Content-Type"], "application/octet-stream")
                return response
            with patch("meural_mcp.api_client.requests.request", side_effect=send) as request:
                self.client.upload_video_file(NAME, GALLERY, path, repeat=3)
                self.client.play_video(NAME, url="https://media.example.test/live", repeat=0)
                self.assertEqual(request.call_args.kwargs["json"]["repeat"], 0)
                self.client.stop_video(NAME, "overlay")
                self.assertEqual(request.call_args.kwargs["json"], {"presentation": "overlay"})

    def test_binary_download_and_409_preservation(self):
        response = Mock(status_code=200, headers={})
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.iter_content.return_value = iter([MEDIA])
        with tempfile.TemporaryDirectory() as folder, patch("meural_mcp.api_client.requests.get", return_value=response):
            path = Path(folder) / "clip"
            result = self.client.download_video_file(NAME, FILE, path, max_bytes=1000)
            self.assertEqual(result["status"], "pass")
            self.assertEqual(path.read_bytes(), MEDIA)
        response.status_code = 409
        response.json.return_value = {"status": "failed", "is_ok": False, "error": "in_use"}
        with patch("meural_mcp.api_client.requests.request", return_value=response):
            self.assertEqual(self.client.delete_video_file(NAME, FILE)["http_status"], 409)


if __name__ == "__main__":
    unittest.main()
