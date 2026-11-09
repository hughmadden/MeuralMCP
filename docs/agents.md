# MCP and REST reference

Everything an agent needs. No knowledge of how the frames are served locally is
required.

Devices are addressed by the configured name (`canvas-1`). Images must match the
device's orientation — landscape is 16:9, portrait is 9:16. A mismatched image is
rejected with `orientation_mismatch` and the dimensions that were found.

## MCP tools

Served over stdio (`meural-mcp mcp`) or as a hosted streamable-HTTP endpoint at
`/mcp/` on the REST API, authenticated with the same bearer token.

### `list_devices`

No arguments. Returns every configured device with name, display name, IP,
orientation, enabled flag, current reachability, and the assigned image path.

### `get_device_status(name)`

One device: the same fields plus frame-reported alias, firmware version and
orientation, its current gallery, and the last recorded state.

### `get_device_image(name)`

The stored image path for a device, and whether one exists.

### `set_device_image_data(name, image_base64, filename)`

Store image bytes uploaded by the client and show them. Use this over remote
transport, where the server cannot see your filesystem.

```json
{"name": "canvas-1", "filename": "poster.jpg", "image_base64": "<base64>"}
```

### `clear_device_image(name)`

Forget the stored image for a device. The frame keeps showing whatever it has.

### `publish_device(name)`

Re-send the stored image to the frame. Images are delivered once, so use this if
a frame has been reset or is showing the wrong thing.

### `sleep_device(name)` / `wake_device(name)`

Suspend or resume the display immediately.

### `get_device_schedule(name)`

The device's sleep window: `enabled`, `timezone`, `sleep_start`, `wake_time`.

### `set_device_schedule(name, sleep_start, wake_time, timezone, enabled)`

Set a daily window in which the frame is suspended, with `HH:MM` times and an IANA
timezone. The daemon applies it at the edges; `sleep_start` may be later than
`wake_time` for a window that crosses midnight.

```json
{"name": "canvas-1", "sleep_start": "22:30", "wake_time": "07:00", "timezone": "Etc/UTC"}
```

### `clear_device_schedule(name)`

Remove the schedule.

## Result shape

Success:

```json
{"status": "pass", "image": "...", "gallery_id": 912345}
```

Failure carries a machine-readable reason:

| Reason | Meaning |
| --- | --- |
| `device_not_found` | unknown name |
| `image_not_found` | path does not exist |
| `image_base64_invalid` | not valid base64 |
| `unreadable_image` | not a decodable JPEG/PNG |
| `orientation_mismatch` | dimensions do not match the device |
| `disabled` | device is disabled in config |
| `invalid_time` | times not `HH:MM` |
| `identical_times` | `sleep_start` equals `wake_time` |
| `unknown_timezone` | not an IANA timezone |

## REST equivalents

Same shapes, bearer token in `Authorization`.

```
GET    /devices
GET    /devices/{name}
GET    /devices/{name}/image          -> image bytes
PUT    /devices/{name}/image          <- raw image bytes, Content-Type image/*
DELETE /devices/{name}/image
GET    /devices/{name}/schedule
PUT    /devices/{name}/schedule        <- JSON body as above
DELETE /devices/{name}/schedule
POST   /devices/{name}/publish
POST   /devices/{name}/sleep
POST   /devices/{name}/wake
GET    /status                        -> service and device summary
GET    /healthz
```

```bash
curl -X PUT http://127.0.0.1:8733/devices/canvas-1/image \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: image/jpeg" \
  --data-binary @poster.jpg
```

## Notes

- Images are stored server-side, so a frame keeps showing the last image after a
  reboot without the agent running.
- `set_device_image_data` fails if the image cannot be decoded or the orientation is
  wrong; nothing is stored on failure.
- Only one image per device is held. Setting another replaces it.
