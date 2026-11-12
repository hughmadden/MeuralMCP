# Deploying MeuralMCP

Operator guide for the on-device-only service. Agent-facing methods are in
[agents.md](agents.md). MeuralMCP drives each frame through its own
MeuralDeviceControl API on `:8099`; no additional frame-facing service is required.

## Requirements

- A host that can reach the frames on the LAN.
- Each frame running MeuralDeviceControl with its unique token in the MeuralMCP config.
- Python 3.11 or later.

Get a frame token using its documented mechanism (for example
`ssh root@FRAME "sh /root/uf.sh token"` when installed). Keep it secret like the
global REST bearer token. Config must be mode 600.

## Install

Install from an exact source release into a virtual environment:

```sh
python3 -m venv /opt/meural-mcp/.venv
/opt/meural-mcp/.venv/bin/pip install /path/to/meural-mcp
```

Create the config and register each frame:

```sh
meural-mcp --storage-dir /var/lib/meural-mcp init
meural-mcp --storage-dir /var/lib/meural-mcp add-device \
  --name canvas-1 --ip 192.0.2.10 --orientation landscape \
  --token "<frame-token>"
```

Example:

```json
{
  "poll_seconds": 60,
  "reload_after_seconds": 0,
  "devices": [
    {
      "name": "canvas-1",
      "display_name": "Canvas 1",
      "local_ip": "192.0.2.10",
      "api_token": "<frame-token>",
      "orientation": "landscape",
      "enabled": true,
      "schedule": {
        "enabled": false,
        "timezone": "Australia/Sydney",
        "sleep_start": "19:30",
        "wake_time": "07:00"
      }
    }
  ]
}
```

`add-device` preserves existing device fields when only a token is updated.
Every configured device needs a token; on-device-only operations return
`on_device_api_required` if one is absent.

## Services

Run the agent-facing REST/MCP API and the sleep daemon using the supplied user or
system units in `examples/systemd/`:

```sh
meural-mcp serve --host 127.0.0.1 --port 8733
meural-mcp daemon
```

Keep the API on loopback and put the existing reverse proxy in front for remote
agents. Samples are in `examples/reverse-proxy/`. Disable response buffering for
MCP and set `MEURAL_MCP_ALLOWED_HOSTS` to the public hostname (including the port
when clients send it) so DNS-rebinding protection remains enabled.


## Publishing and status

`POST /devices/<name>/image` stores the supplied image on the service host,
uploads it into a managed on-device gallery, removes stale items, and selects the
gallery and new item. `POST /devices/<name>/publish` repeats that operation
without a new upload. Publishing is immediate; no periodic reapply is required.

Use `GET /devices` for inventory and `GET /devices/<name>` for current status.
`gallery_id` is `null` until the first direct publish. Galleries, settings,
actions, and immediate pictures use the routes in [agents.md](agents.md).

## Sleep schedules

Per-device suspend windows are set in config or over MCP
(`set_device_schedule`) and applied by `meural-mcp daemon`. See
[sleep-schedules.md](sleep-schedules.md).

## Security and verification

- Frame tokens are full-control credentials and plaintext on the LAN. Restrict
  access to the frame and service-host networks, keep config mode 600, and do not log
  tokens or raw settings.
- Settings reads redact credential-like values unless `include_sensitive=true`
  is explicitly requested.
- Power, network, input, gallery-delete, and item-delete operations can be
  disruptive; invoke them deliberately.

Verify without changing the displayed image:

1. `GET /healthz` returns `{"ok": true}`.
2. Authenticated `GET /devices` returns the expected inventory.
3. `GET /devices/<name>/time`, `/galleries`, and `/settings/schema` return
   `status=pass` for each tokenized frame.
4. Use a temporary gallery to verify create/upload/read/delete, then remove it.
5. Publish the stored image and confirm the frame reports the expected gallery
   and current item.

## Video storage and preparation

The frame must run a MeuralDeviceControl release implementing `/local/video`,
persistent video galleries/files, storage reporting, repeat counts and layered
playback. Check capabilities before using a layer. The host forwards prepared
media, using file handles to send raw binary bodies to the frame; it never asks
the frame to encode, resize or rotate media. Prepare MP4 H.264 Baseline/YUV420P
on a suitable host at the intended orientation and display size.

Transparent overlays use native `.mvo` bundles, media type
`application/vnd.meural.overlay`. The `MVOZ0001` format contains compressed
premultiplied BGRA frames at the native canvas dimensions, preserving per-pixel
alpha while the native gallery continues underneath. Prepare them upstream with
[`tools/prepare_media.py` in MeuralDeviceControl](https://github.com/hughmadden/MeuralDeviceControl/blob/main/tools/prepare_media.py):

```sh
python3 /path/to/MeuralDeviceControl/tools/prepare_media.py overlay \
  rgba-frames/ overlay.mvo --width 1080 --height 1920
```

Use the actual canvas dimensions reported by capabilities; the values above are
an example. Upload the resulting bundle with `filename=overlay.mvo` and
`kind=overlay`. Binary REST transport still uses `application/octet-stream`.
The service forwards the bundle unchanged. Encoding, resizing, orientation and
alpha preparation happen on the host; the frame only decodes and displays it.

REST upload spools request chunks to temporary host storage, checks required
Content-Length against the frame's available-upload bytes and host free space,
and cleans the spool on success or failure. A 64 MiB reserve is retained on the
host temporary filesystem. REST downloads also spool with capacity checks and
remove their temporary file after response delivery. Set the service's TMPDIR
to a filesystem with room for the largest expected transfer. REST uploads, MCP
uploads and downloads share disk admission and bounded writes. Known-length
transfers reserve their remaining bytes; written bytes count through actual disk
usage. Download spools stay on disk until response completion or disconnect.

Run one API worker/process per temporary filesystem: spool reservations and their
lock are process-local. Concurrent transfers within that process share admission;
multiple worker processes or service instances do not share reservations and can
consume the reserve. No fixed REST media-size cap is imposed. Each frame retains
its own disk reserve and rechecks storage when receiving bytes.

MCP base64 upload/download is limited to 16 MiB decoded bytes. Use the binary
REST routes for larger media and configure proxy request-size/time limits to
match expected media. Transfers use a 300-second frame HTTP timeout.
`repeat=1` plays once, `0` repeats indefinitely and `N` counts complete playlist
cycles. Fullscreen completion/error/stop restores the original native gallery
and item; overlay leaves the native gallery alive beneath it. Stop the relevant
layer explicitly before deleting active content (409); gallery deletion also
removes its owned media. No automatic destructive stop is performed.

Verify status, storage, persistent gallery create/upload/details/download/delete,
file/gallery/URL playback, repeat counts and stop-by-layer using prepared test
media. Confirm the physical fullscreen restore and overlay behavior on hardware;
unit tests only verify forwarding contracts and transfer/error handling.
