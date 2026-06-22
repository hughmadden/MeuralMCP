import json
import os
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse

from .manager import ManagerService, auth_token_valid, load_config


app = FastAPI(title="MeuralMCP Manager")


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


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/devices", dependencies=[Depends(authenticated)])
def list_devices():
    manager = service()
    return {"devices": [manager.device_status(device["name"]) for device in manager.devices()]}


@app.get("/status", dependencies=[Depends(authenticated)])
def summary_status():
    return service().summary_status()


@app.get("/devices/{name}", dependencies=[Depends(authenticated)])
def get_device(name: str):
    try:
        return service().device_status(name)
    except KeyError:
        raise HTTPException(status_code=404, detail="device not found")


@app.get("/devices/{name}/image", dependencies=[Depends(authenticated)])
def get_device_image(name: str):
    manager = service()
    try:
        image = manager.image_path(name)
    except KeyError:
        raise HTTPException(status_code=404, detail="device not found")
    if not image:
        raise HTTPException(status_code=404, detail="image not found")
    return FileResponse(image)


@app.put("/devices/{name}/image", dependencies=[Depends(authenticated)])
@app.post("/devices/{name}/image", dependencies=[Depends(authenticated)])
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


@app.post("/devices/{name}/preview", dependencies=[Depends(authenticated)])
def retry_preview(name: str):
    try:
        result = service().preview_stored_image(name)
    except KeyError:
        raise HTTPException(status_code=404, detail="device not found")
    status = 200 if result.get("status") == "loaded" else 422
    return Response(json.dumps(result), status_code=status, media_type="application/json")
