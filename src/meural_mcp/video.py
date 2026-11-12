"""Prepared video transfer limits and playback validation shared by surfaces.

This service forwards prepared bytes. It never converts media on a frame.
Native .mvo overlay bundles are forwarded unchanged; capabilities govern playback.
"""

import os
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

MCP_VIDEO_MAX_BYTES = 16 * 1024 * 1024
HOST_SPOOL_RESERVE_BYTES = 64 * 1024 * 1024
TRANSFER_CHUNK_BYTES = 256 * 1024
Presentation = Literal["auto", "fullscreen", "overlay"]
StopPresentation = Literal["all", "fullscreen", "overlay"]
MediaKind = Literal["video", "overlay"]


class VideoTransferError(ValueError):
    def __init__(self, message: str, http_status: int):
        self.http_status = http_status
        super().__init__(message)


# One owner for every prepared-media spool in this process. Reservations count
# only unwritten bytes: written bytes are already reflected by disk_usage().
_SPOOL_LOCK = threading.RLock()
_SPOOL_RESERVED: dict[int, int] = {}


def _spool_unreserve(device: int, amount: int) -> None:
    if amount:
        outstanding = _SPOOL_RESERVED.get(device, 0) - amount
        if outstanding > 0:
            _SPOOL_RESERVED[device] = outstanding
        else:
            _SPOOL_RESERVED.pop(device, None)


def _spool_location(location: Path | None) -> Path:
    return Path(location) if location is not None else Path(tempfile.gettempdir())


def _spool_capacity(location: Path) -> int:
    device = location.stat().st_dev
    return (shutil.disk_usage(location).free - HOST_SPOOL_RESERVE_BYTES
            - _SPOOL_RESERVED.get(device, 0))


def spool_available_bytes(location: Path | None = None) -> int:
    with _SPOOL_LOCK:
        return max(0, _spool_capacity(_spool_location(location)))


def transfer_failure(error: VideoTransferError) -> dict:
    return {"status": "failed", "is_ok": False, "reason": "transfer_failed",
            "error": str(error), "http_status": error.http_status}


def _storage_error() -> VideoTransferError:
    # Never expose a host path, source URL or credential from an OSError.
    return VideoTransferError("host temporary storage operation failed", 507)


class HostSpool:
    """A private file with shared, filesystem-scoped admission and bounded writes.

    Network reads happen outside this owner. The lock covers only disk admission,
    writes and accounting. Successful close releases unwritten reservations but
    leaves the physical file for forwarding or response delivery; its actual disk
    usage continues to count until its caller removes it.
    """

    def __init__(self, length: int | None = None, *, destination: Path | None = None,
                 prefix: str = "meural-video-"):
        if length is not None and (type(length) is not int or length < 0):
            raise ValueError("spool length must be a nonnegative integer")
        self.length = length
        self.remaining = length or 0
        self.path = destination
        self.location = _spool_location(destination.parent if destination is not None else None)
        self.prefix = prefix
        self._fd = None
        self._device = None
        self._admitted = False

    def _allocated(self, length: int) -> int:
        info = os.statvfs(self.location)
        block = info.f_frsize or info.f_bsize or 4096
        return ((length + block - 1) // block) * block

    def _release(self) -> None:
        if self._admitted:
            _spool_unreserve(self._device, self.remaining)
            self.remaining = 0
            self._admitted = False

    def __enter__(self):
        with _SPOOL_LOCK:
            try:
                if _spool_capacity(self.location) < self._allocated(self.remaining):
                    raise VideoTransferError("insufficient host temporary storage", 507)
                self._device = self.location.stat().st_dev
                if self.remaining:
                    _SPOOL_RESERVED[self._device] = _SPOOL_RESERVED.get(self._device, 0) + self.remaining
                self._admitted = True
                if self.path is None:
                    self._fd, name = tempfile.mkstemp(prefix=self.prefix, dir=self.location)
                    self.path = Path(name)
                else:
                    flags = os.O_CREAT | os.O_TRUNC | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
                    self._fd = os.open(self.path, flags, 0o600)
                os.fchmod(self._fd, 0o600)
                if _spool_capacity(self.location) < 0:
                    raise VideoTransferError("insufficient host temporary storage", 507)
                return self
            except BaseException as error:
                try:
                    self.close(sync=False)
                finally:
                    if self._admitted or self._fd is not None:
                        self._release()
                    if self.path is not None:
                        self.path.unlink(missing_ok=True)
                if isinstance(error, OSError):
                    raise _storage_error() from None
                raise

    def write(self, data: bytes) -> int:
        view = memoryview(data)
        if self.length is not None and len(view) > self.remaining:
            raise VideoTransferError("media exceeds declared length", 400)
        offset = 0
        while offset < len(view):
            chunk = view[offset:offset + TRANSFER_CHUNK_BYTES]
            with _SPOOL_LOCK:
                try:
                    if self._fd is None:
                        raise ValueError("spool is closed")
                    # This write consumes its own known-length reservation. An
                    # unknown-length write must fit beyond all existing promises.
                    promised = min(len(chunk), self.remaining)
                    if _spool_capacity(self.location) < self._allocated(len(chunk)) - promised:
                        raise VideoTransferError("insufficient host temporary storage", 507)
                    written = os.write(self._fd, chunk)
                    if written <= 0:
                        raise _storage_error()
                    if self.length is not None:
                        self.remaining -= written
                        _spool_unreserve(self._device, written)
                    offset += written
                    if _spool_capacity(self.location) < 0:
                        raise VideoTransferError("insufficient host temporary storage", 507)
                except OSError:
                    raise _storage_error() from None
        return offset

    def close(self, *, sync: bool = True) -> None:
        failure = None
        with _SPOOL_LOCK:
            try:
                if self._fd is not None:
                    try:
                        if sync:
                            os.fsync(self._fd)
                    except OSError:
                        failure = _storage_error()
                    finally:
                        try:
                            os.close(self._fd)
                        except OSError:
                            failure = _storage_error()
                        self._fd = None
                    # Some filesystems settle allocation at fsync/close. Keep
                    # other promises and the floor intact before releasing ours.
                    if failure is None:
                        try:
                            if _spool_capacity(self.location) < 0:
                                failure = VideoTransferError("insufficient host temporary storage", 507)
                        except OSError:
                            failure = _storage_error()
            finally:
                self._release()
        if failure is not None:
            raise failure

    def __exit__(self, exc_type, exc, traceback):
        try:
            self.close(sync=exc_type is None)
        except VideoTransferError:
            if self.path is not None:
                self.path.unlink(missing_ok=True)
            if exc_type is None:
                raise
        finally:
            if exc_type is not None and self.path is not None:
                self.path.unlink(missing_ok=True)
        return False


def new_spool_path(prefix: str) -> Path:
    with HostSpool(0, prefix=prefix) as spool:
        return spool.path


def validate_presentation(presentation: str, *, stop: bool = False) -> None:
    allowed = {"all", "fullscreen", "overlay"} if stop else {"auto", "fullscreen", "overlay"}
    if presentation not in allowed:
        raise ValueError("invalid presentation")


def validate_repeat(repeat: int) -> None:
    if type(repeat) is not int or repeat < 0:
        raise ValueError("repeat must be a nonnegative integer (0 repeats forever)")


def playback_payload(*, url: str | None = None, file_id: str | None = None,
                     gallery_id: str | None = None, repeat: int = 1,
                     presentation: str = "auto") -> dict:
    validate_repeat(repeat)
    validate_presentation(presentation)
    sources = {"url": url, "file_id": file_id, "gallery_id": gallery_id}
    provided = {key: value for key, value in sources.items() if value is not None}
    if len(provided) != 1 or any(not isinstance(value, str) or not value.strip()
                                 for value in provided.values()):
        raise ValueError("provide exactly one nonempty url, file_id or gallery_id")
    if url is not None:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("url must be an absolute HTTP or HTTPS URL")
    return {**provided, "repeat": repeat, "presentation": presentation}


def upload_options(filename: str, kind: str, title: str | None, play: bool,
                   repeat: int, presentation: str) -> dict:
    validate_repeat(repeat)
    validate_presentation(presentation)
    if kind not in {"video", "overlay"}:
        raise ValueError("kind must be video or overlay")
    if not filename or Path(filename).name != filename or "\\" in filename or "\x00" in filename:
        raise ValueError("filename must be a plain filename")
    if type(play) is not bool:
        raise ValueError("play must be a boolean")
    return {"filename": filename, "kind": kind, "title": title,
            "play": play, "repeat": repeat, "presentation": presentation}


def resource_id(value: str | int) -> str:
    """Reject route traversal rather than interpolating arbitrary paths."""
    identifier = str(value)
    if not identifier or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in identifier):
        raise ValueError("invalid media id")
    return identifier


def copy_video_download(response, destination: Path, max_bytes: int | None) -> dict:
    """Stream into a caller-owned temporary path using shared disk admission."""
    size = 0
    try:
        declared = response.headers.get("Content-Length")
        length = int(declared) if declared is not None else None
        if length is not None and length < 0:
            raise ValueError("invalid media Content-Length")
        if max_bytes is not None and length is not None and length > max_bytes:
            raise ValueError("video download exceeds transfer limit; use REST for larger files")
        # requests decodes Content-Encoding while iterating; its wire length then
        # cannot serve as a reservation for the resulting prepared bytes.
        if response.headers.get("Content-Encoding", "identity").lower() != "identity":
            length = None
        with HostSpool(length, destination=destination) as media:
            for chunk in response.iter_content(chunk_size=TRANSFER_CHUNK_BYTES):
                size += len(chunk)
                if max_bytes is not None and size > max_bytes:
                    raise ValueError("video download exceeds transfer limit; use REST for larger files")
                media.write(chunk)
            if length is not None and size != length:
                raise VideoTransferError("incomplete media download", 502)
        return {"is_ok": True, "size_bytes": size}
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
