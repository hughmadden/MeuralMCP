import json
import os
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator
from starlette.concurrency import run_in_threadpool

from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .manager import ManagerService, auth_token_valid, load_config
from .video import (HostSpool, MediaKind, Presentation, StopPresentation, VideoTransferError,
                    new_spool_path, playback_payload, spool_available_bytes,
                    transfer_failure, upload_options)
from .mcp_server import build_mcp_server


class BearerTokenASGIMiddleware:
    def __init__(self, app: Callable):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return

        expected = os.getenv("MEURAL_MCP_API_TOKEN") or load_config().get("api_token")
        if not expected:
            await self._send_json(send, 503, {"detail": "api token not configured"})
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        authorization = headers.get(b"authorization", b"").decode("latin1")
        x_token = headers.get(b"x-meural-mcp-token", b"").decode("latin1")
        if not auth_token_valid(expected, authorization, x_token):
            await self._send_json(send, 401, {"detail": "unauthorized"})
            return

        await self.app(scope, receive, send)

    async def _send_json(self, send, status: int, body: dict) -> None:
        payload = json.dumps(body).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(payload)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": payload})


def _data_file(name: str) -> PlainTextResponse:
    """Serve a packaged agent doc, so the service documents itself."""
    path = Path(__file__).parent / "data" / name
    text = path.read_text() if path.exists() else "not packaged"
    return PlainTextResponse(text)


def require_api_token(
    expected_token: Optional[str],
    authorization: Optional[str] = None,
    x_meural_mcp_token: Optional[str] = None,
) -> None:
    if not auth_token_valid(expected_token, authorization, x_meural_mcp_token):
        raise HTTPException(status_code=401, detail="unauthorized")


bearer_scheme = HTTPBearer(auto_error=False, description="Bearer token from config.json (meural-mcp init)")


def authenticated(
    authorization: Optional[str] = Header(default=None),
    x_meural_mcp_token: Optional[str] = Header(default=None, alias="X-Meural-MCP-Token"),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> None:
    expected = os.getenv("MEURAL_MCP_API_TOKEN") or load_config().get("api_token")
    if not expected:
        raise HTTPException(status_code=503, detail="api token not configured")
    require_api_token(expected, authorization, x_meural_mcp_token)


def service() -> ManagerService:
    return ManagerService()


def feature_response(call: Callable, *args) -> Response:
    """Return a consistent HTTP result for on-device feature calls."""
    try:
        result = call(*args)
    except KeyError:
        raise HTTPException(status_code=404, detail="device not found")
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    status = 200 if result.get("status") in {"pass", "partial"} else 422
    return Response(json.dumps(result), status_code=status, media_type="application/json")


class VideoGalleryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1)


class VideoPlay(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str | None = None
    file_id: str | None = None
    gallery_id: str | None = None
    repeat: StrictInt = Field(default=1, ge=0, description="Total cycles; 0 repeats forever")
    presentation: Presentation = "auto"

    @model_validator(mode="after")
    def validate_source(self):
        playback_payload(**self.model_dump())
        return self


class VideoStop(BaseModel):
    model_config = ConfigDict(extra="forbid")
    presentation: StopPresentation = "all"


def video_result(call: Callable, *args, **kwargs) -> dict:
    try:
        return call(*args, **kwargs)
    except KeyError:
        raise HTTPException(status_code=404, detail="device not found")
    except VideoTransferError as exc:
        return transfer_failure(exc)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))


def video_response(call: Callable, *args, **kwargs) -> Response:
    result = video_result(call, *args, **kwargs)
    status = 200 if result.get("status") == "pass" else result.get("http_status", 422)
    return Response(json.dumps(result), status_code=status, media_type="application/json")


class TemporaryMediaResponse(FileResponse):
    """Remove a transfer spool even when response delivery fails or disconnects."""

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            Path(self.path).unlink(missing_ok=True)


def mcp_allowed_hosts() -> list[str]:
    defaults = ["127.0.0.1", "127.0.0.1:*", "localhost", "localhost:*", "testserver", "testserver:*"]
    configured = os.getenv("MEURAL_MCP_ALLOWED_HOSTS", "")
    extras = [host.strip() for host in configured.split(",") if host.strip()]
    return defaults + extras


def create_app() -> FastAPI:
    remote_mcp_server = build_mcp_server(
        streamable_http_path="/",
        stateless_http=True,
        allowed_hosts=mcp_allowed_hosts(),
    )

    @asynccontextmanager
    async def lifespan(api_app: FastAPI):
        async with remote_mcp_server.session_manager.run():
            yield

    api_app = FastAPI(
        title="MeuralMCP",
        description="Local Meural frame control. Agent guide: /llms.txt (full: /agents.md). "
                    "All routes except /healthz, /llms.txt and /agents.md need a bearer token.",
        lifespan=lifespan,
    )
    remote_mcp_app = remote_mcp_server.streamable_http_app()
    api_app.mount("/mcp", BearerTokenASGIMiddleware(remote_mcp_app))
    register_routes(api_app)
    return api_app


def register_routes(api_app: FastAPI) -> None:
    @api_app.get("/healthz")
    def healthz():
        return {"ok": True}

    @api_app.get("/llms.txt", include_in_schema=False)
    def llms():
        return _data_file("llms.txt")

    @api_app.get("/agents.md", include_in_schema=False)
    def agents_doc():
        return _data_file("agents.md")

    @api_app.get("/devices", dependencies=[Depends(authenticated)])
    def list_devices():
        manager = service()
        return {"devices": [manager.device_status(device["name"]) for device in manager.devices()]}

    @api_app.get("/status", dependencies=[Depends(authenticated)])
    def summary_status():
        return service().summary_status()

    @api_app.get("/devices/{name}", dependencies=[Depends(authenticated)])
    def get_device(name: str):
        try:
            return service().device_status(name)
        except KeyError:
            raise HTTPException(status_code=404, detail="device not found")

    @api_app.get("/devices/{name}/image", dependencies=[Depends(authenticated)])
    def get_device_image(name: str):
        manager = service()
        try:
            image = manager.image_path(name)
        except KeyError:
            raise HTTPException(status_code=404, detail="device not found")
        if not image:
            raise HTTPException(status_code=404, detail="image not found")
        return FileResponse(image)

    @api_app.put("/devices/{name}/image", dependencies=[Depends(authenticated)])
    @api_app.post("/devices/{name}/image", dependencies=[Depends(authenticated)])
    async def put_device_image(name: str, request: Request):
        body = await request.body()
        if not body:
            raise HTTPException(status_code=400, detail="empty image body")
        suffix = ".png" if "png" in request.headers.get("content-type", "").lower() else ".jpg"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(body)
            tmp_path = Path(tmp.name)
        try:
            result = service().assign_image(name, tmp_path)
        except KeyError:
            raise HTTPException(status_code=404, detail="device not found")
        finally:
            tmp_path.unlink(missing_ok=True)
        return Response(json.dumps(result), status_code=200 if result.get("status") == "pass" else 422,
                        media_type="application/json")

    @api_app.delete("/devices/{name}/image", dependencies=[Depends(authenticated)])
    def delete_device_image(name: str):
        try:
            return service().clear_image(name)
        except KeyError:
            raise HTTPException(status_code=404, detail="device not found")

    @api_app.post("/devices/{name}/publish", dependencies=[Depends(authenticated)])
    def publish_device(name: str):
        """Re-publish the stored image directly to the frame's on-device API."""
        try:
            return service().publish(name)
        except KeyError:
            raise HTTPException(status_code=404, detail="device not found")

    @api_app.get("/devices/{name}/video", dependencies=[Depends(authenticated)])
    def get_device_video(name: str):
        """Get video capabilities, fullscreen/overlay layers and disk storage."""
        return video_response(service().video_status, name)

    @api_app.get("/devices/{name}/video/storage", dependencies=[Depends(authenticated)])
    def get_device_video_storage(name: str):
        return video_response(service().video_storage, name)

    @api_app.post("/devices/{name}/video", dependencies=[Depends(authenticated)])
    def play_device_video(name: str, payload: VideoPlay):
        """Play a prepared file, gallery or HTTP(S) source; repeat counts full cycles."""
        return video_response(service().play_video, name, **payload.model_dump())

    @api_app.post("/devices/{name}/video/stop", dependencies=[Depends(authenticated)])
    def stop_device_video(name: str, payload: VideoStop = VideoStop()):
        return video_response(service().stop_video, name, payload.presentation)

    @api_app.get("/devices/{name}/video/galleries", dependencies=[Depends(authenticated)])
    def list_device_video_galleries(name: str):
        return video_response(service().list_video_galleries, name)

    @api_app.post("/devices/{name}/video/galleries", dependencies=[Depends(authenticated)])
    def create_device_video_gallery(name: str, payload: VideoGalleryCreate):
        return video_response(service().create_video_gallery, name, payload.name)

    @api_app.get("/devices/{name}/video/galleries/{gallery_id}", dependencies=[Depends(authenticated)])
    def get_device_video_gallery(name: str, gallery_id: str):
        return video_response(service().get_video_gallery, name, gallery_id)

    @api_app.delete("/devices/{name}/video/galleries/{gallery_id}", dependencies=[Depends(authenticated)])
    def delete_device_video_gallery(name: str, gallery_id: str):
        return video_response(service().delete_video_gallery, name, gallery_id)

    @api_app.get("/devices/{name}/video/files", dependencies=[Depends(authenticated)])
    def list_device_video_files(name: str):
        return video_response(service().list_video_files, name)

    @api_app.get("/devices/{name}/video/files/{file_id}", dependencies=[Depends(authenticated)])
    def get_device_video_file(name: str, file_id: str):
        return video_response(service().get_video_file, name, file_id)

    @api_app.delete("/devices/{name}/video/files/{file_id}", dependencies=[Depends(authenticated)])
    def delete_device_video_file(name: str, file_id: str):
        return video_response(service().delete_video_file, name, file_id)

    @api_app.post("/devices/{name}/video/galleries/{gallery_id}/files",
                  dependencies=[Depends(authenticated)], openapi_extra={
                      "requestBody": {"required": True, "content": {
                          "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}}})
    async def upload_device_video_file(name: str, gallery_id: str, request: Request,
                                       filename: str = "video.mp4", kind: MediaKind = "video",
                                       title: str | None = None, play: bool = False,
                                       repeat: int = 1, presentation: Presentation = "auto"):
        """Upload raw prepared bytes with Content-Length; larger files use bounded disk spooling."""
        try:
            options = upload_options(filename, kind, title, play, repeat, presentation)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        length_header = request.headers.get("content-length")
        if length_header is None:
            raise HTTPException(status_code=411, detail="Content-Length is required")
        try:
            length = int(length_header)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid Content-Length")
        if length <= 0:
            raise HTTPException(status_code=400, detail="empty media body")
        manager = service()
        storage = await run_in_threadpool(video_result, manager.video_storage, name)
        if storage.get("status") != "pass":
            return video_response(lambda: storage)
        available = storage.get("storage", {}).get("available_upload_bytes")
        if type(available) is not int or available < 0:
            raise HTTPException(status_code=502, detail="device upload capacity unavailable")
        if length > available:
            raise HTTPException(status_code=507, detail="insufficient device storage")
        if length > spool_available_bytes():
            raise HTTPException(status_code=507, detail="insufficient host temporary storage")
        path = None
        try:
            with HostSpool(length) as media:
                path = media.path
                size = 0
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > length:
                        raise HTTPException(status_code=400, detail="body exceeds Content-Length")
                    await run_in_threadpool(media.write, chunk)
                if size != length:
                    raise HTTPException(status_code=400, detail="body differs from Content-Length")
            return await run_in_threadpool(video_response, manager.upload_video_file,
                                           name, gallery_id, path, **options)
        except VideoTransferError as exc:
            return video_response(lambda: transfer_failure(exc))
        finally:
            if path is not None:
                path.unlink(missing_ok=True)

    @api_app.get("/devices/{name}/video/files/{file_id}/data", dependencies=[Depends(authenticated)],
                 responses={200: {"content": {"application/octet-stream": {}}}})
    def download_device_video_file(name: str, file_id: str):
        """Download prepared bytes without embedding them in a JSON response."""
        manager = service()
        metadata = video_result(manager.get_video_file, name, file_id)
        if metadata.get("status") != "pass":
            return video_response(lambda: metadata)
        size = metadata.get("file", {}).get("bytes")
        available = spool_available_bytes()
        if type(size) is not int or size < 0:
            raise HTTPException(status_code=502, detail="device file size unavailable")
        if size > available:
            raise HTTPException(status_code=507, detail="insufficient host temporary storage")
        path = None
        try:
            path = new_spool_path("meural-video-download-")
            result = video_result(manager.download_video_file, name, file_id, path, max_bytes=available)
            if result.get("status") != "pass":
                path.unlink(missing_ok=True)
                return video_response(lambda: result)
            return TemporaryMediaResponse(path, media_type="application/octet-stream")
        except VideoTransferError as exc:
            if path is not None:
                path.unlink(missing_ok=True)
            return video_response(lambda: transfer_failure(exc))
        except BaseException:
            if path is not None:
                path.unlink(missing_ok=True)
            raise

    @api_app.get("/devices/{name}/time", dependencies=[Depends(authenticated)])
    def device_time(name: str):
        return feature_response(service().device_time, name)

    @api_app.get("/devices/{name}/galleries", dependencies=[Depends(authenticated)])
    def list_galleries(name: str):
        return feature_response(service().list_galleries, name)

    @api_app.post("/devices/{name}/galleries", dependencies=[Depends(authenticated)])
    def create_gallery(name: str, payload: dict):
        return feature_response(service().create_gallery, name, payload.get("name", ""))

    @api_app.get("/devices/{name}/galleries/{gallery_id}", dependencies=[Depends(authenticated)])
    def get_gallery(name: str, gallery_id: str):
        return feature_response(service().get_gallery, name, gallery_id)

    @api_app.post("/devices/{name}/galleries/{gallery_id}/items", dependencies=[Depends(authenticated)])
    async def upload_gallery_item(name: str, gallery_id: str, request: Request, title: Optional[str] = None):
        body = await request.body()
        if not body:
            raise HTTPException(status_code=400, detail="empty image body")
        suffix = ".png" if "png" in request.headers.get("content-type", "").lower() else ".jpg"
        return feature_response(service().upload_gallery_item, name, gallery_id, body, suffix, title)

    @api_app.post("/devices/{name}/galleries/{gallery_id}/show", dependencies=[Depends(authenticated)])
    def show_gallery(name: str, gallery_id: str):
        return feature_response(service().show_gallery, name, gallery_id)

    @api_app.delete("/devices/{name}/galleries/{gallery_id}", dependencies=[Depends(authenticated)])
    def delete_gallery(name: str, gallery_id: str):
        return feature_response(service().delete_gallery, name, gallery_id)

    @api_app.post("/devices/{name}/items/{item_id}/show", dependencies=[Depends(authenticated)])
    def show_item(name: str, item_id: str):
        return feature_response(service().show_item, name, item_id)

    @api_app.delete("/devices/{name}/items/{item_id}", dependencies=[Depends(authenticated)])
    def delete_item(name: str, item_id: str):
        return feature_response(service().delete_item, name, item_id)

    @api_app.post("/devices/{name}/picture", dependencies=[Depends(authenticated)])
    def set_picture(name: str, payload: dict):
        try:
            timeout = int(payload.get("timeout", 600))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=f"invalid timeout: {exc}")
        return feature_response(service().set_picture, name, payload.get("path", ""), timeout)

    @api_app.get("/devices/{name}/settings", dependencies=[Depends(authenticated)])
    def get_settings(name: str, include_sensitive: bool = False):
        return feature_response(service().get_settings, name, include_sensitive)

    @api_app.get("/devices/{name}/settings/schema", dependencies=[Depends(authenticated)])
    def get_settings_schema(name: str):
        return feature_response(service().get_settings_schema, name)

    @api_app.post("/devices/{name}/settings", dependencies=[Depends(authenticated)])
    def update_settings(name: str, payload: dict):
        return feature_response(service().update_settings, name, payload)

    @api_app.post("/devices/{name}/action", dependencies=[Depends(authenticated)])
    def run_action(name: str, payload: dict):
        action = payload.get("action") or payload.get("do") or ""
        return feature_response(service().run_action, name, action, payload.get("value"), payload.get("value2"))

    @api_app.get("/devices/{name}/schedule", dependencies=[Depends(authenticated)])
    def get_device_schedule(name: str):
        try:
            return service().get_schedule(name)
        except KeyError:
            raise HTTPException(status_code=404, detail="device not found")

    @api_app.put("/devices/{name}/schedule", dependencies=[Depends(authenticated)])
    def put_device_schedule(name: str, payload: dict):
        try:
            result = service().set_schedule(
                name,
                payload.get("sleep_start", "19:30"),
                payload.get("wake_time", "07:00"),
                payload.get("timezone", "Etc/UTC"),
                bool(payload.get("enabled", True)),
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="device not found")
        return Response(json.dumps(result), status_code=200 if result.get("status") == "pass" else 422,
                        media_type="application/json")

    @api_app.delete("/devices/{name}/schedule", dependencies=[Depends(authenticated)])
    def delete_device_schedule(name: str):
        try:
            return service().clear_schedule(name)
        except KeyError:
            raise HTTPException(status_code=404, detail="device not found")

    @api_app.post("/devices/{name}/sleep", dependencies=[Depends(authenticated)])
    def sleep_device(name: str):
        try:
            return service().sleep(name)
        except KeyError:
            raise HTTPException(status_code=404, detail="device not found")

    @api_app.post("/devices/{name}/wake", dependencies=[Depends(authenticated)])
    def wake_device(name: str):
        try:
            return service().wake(name)
        except KeyError:
            raise HTTPException(status_code=404, detail="device not found")


app = create_app()
