import json
import os
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse

from .manager import ManagerService, auth_token_valid, load_config
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


def require_api_token(
    expected_token: Optional[str],
    authorization: Optional[str] = None,
    x_meural_mcp_token: Optional[str] = None,
) -> None:
    if not auth_token_valid(expected_token, authorization, x_meural_mcp_token):
        raise HTTPException(status_code=401, detail="unauthorized")


def authenticated(
    authorization: Optional[str] = Header(default=None),
    x_meural_mcp_token: Optional[str] = Header(default=None, alias="X-Meural-MCP-Token"),
) -> None:
    expected = os.getenv("MEURAL_MCP_API_TOKEN") or load_config().get("api_token")
    if not expected:
        raise HTTPException(status_code=503, detail="api token not configured")
    require_api_token(expected, authorization, x_meural_mcp_token)


def service() -> ManagerService:
    return ManagerService()


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

    api_app = FastAPI(title="MeuralMCP Manager", lifespan=lifespan)
    remote_mcp_app = remote_mcp_server.streamable_http_app()
    api_app.mount("/mcp", BearerTokenASGIMiddleware(remote_mcp_app))
    register_routes(api_app)
    return api_app


def register_routes(api_app: FastAPI) -> None:
    @api_app.get("/healthz")
    def healthz():
        return {"ok": True}

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
        status = 200 if result.get("status") == "loaded" else 422
        return Response(json.dumps(result), status_code=status, media_type="application/json")

    @api_app.post("/devices/{name}/preview", dependencies=[Depends(authenticated)])
    def retry_preview(name: str):
        try:
            result = service().preview_stored_image(name)
        except KeyError:
            raise HTTPException(status_code=404, detail="device not found")
        status = 200 if result.get("status") == "loaded" else 422
        return Response(json.dumps(result), status_code=status, media_type="application/json")


app = create_app()
