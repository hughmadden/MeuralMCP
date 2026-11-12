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

No arguments. Returns `status=pass`, device counts, and every configured device
with name, display name, IP, orientation, enabled flag, current reachability,
assigned image, and current gallery. Full historical state is available through
`get_device_status`.

### `get_device_status(name)`

One device: the same fields plus frame-reported alias, firmware version and
orientation, its current gallery, and the on-device gallery state.

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

### `get_device_time(name)`

Return the frame's current epoch time.

### `list_device_galleries(name)` / `get_device_gallery(name, gallery_id)`

List on-device galleries and item counts, or fetch one gallery with its items.

### `create_device_gallery(name, gallery_name)`

Create an on-device gallery.

### `upload_device_gallery_item(name, gallery_id, image_base64, filename, title)`

Upload base64 image bytes into an on-device gallery and return its item id.

### `show_device_gallery(name, gallery_id)` / `show_device_item(name, item_id)`

Make a gallery current or display one item immediately.

### `delete_device_gallery(name, gallery_id)` / `delete_device_item(name, item_id)`

Delete an on-device gallery (the frame keeps items) or an item and its file/links.
These are destructive operations.

### `set_device_picture(name, path, timeout=600)`

Display an image path that already exists **on the frame**. `timeout=0` keeps it
until replaced. This is not a server-side path and does not upload bytes.

### `get_device_settings(name, include_sensitive=False)`

Read frame settings and hardware values. Credential-like fields are redacted by
default. Set `include_sensitive=true` only when raw secret values are required;
do not log the result.

### `get_device_settings_schema(name)`

Return every settable frame column and its type.

### `update_device_settings(name, values)`

Write one or more settings. The frame type-checks values. Results include
`applied`, `rejected`, and `unknown`; status is `partial` when some values applied
and others failed, and `failed` when none applied.

### `run_device_action(name, action, value, value2)`

Run a frame action with optional arguments. This exposes the frame's display,
power, input, network, Bluetooth, locale, RTC and ambient-light actions. Power,
network, and input actions can be disruptive. Values are validated by the frame.

## Prepared video and streams

Video tools preserve prepared media in named galleries. Inspect
`get_device_video(name)` before choosing a presentation: it returns capabilities,
`layers.fullscreen`, `layers.overlay`, compatibility playback status and storage.
`get_device_video_storage(name)` returns `storage` with `total_bytes`, `free_bytes`,
`reserve_bytes`, `available_upload_bytes`, `used_bytes` and `file_count`.

Prepare media before uploading. Normal video uses MP4 with H.264 Baseline and
YUV420P pixels at the intended display size/orientation. Transparent overlays use
prepared `.mvo` bundles (media type `application/vnd.meural.overlay`): the
`MVOZ0001` format carries compressed premultiplied BGRA frames at the native
canvas dimensions, with true per-pixel alpha. The service forwards prepared bytes;
all encoding, resizing, rotation and alpha preparation happen upstream. The frame
decodes and displays the result. Check advertised capabilities before choosing a
presentation. See the operator preparation guide for the host preparation tool.

### Video gallery tools

- `list_device_video_galleries(name)` returns `galleries`.
- `create_device_video_gallery(name, gallery_name)` creates a persistent gallery.
- `get_device_video_gallery(name, gallery_id)` returns `gallery`, including its
  `id`, `name`, `created_at`, `file_count`, `bytes` and `files`.
- `delete_device_video_gallery(name, gallery_id)` deletes the gallery **and its
  files**. Active content returns HTTP 409; explicitly stop playback first.

### Video file tools

- `list_device_video_files(name)` lists persistent prepared files.
- `get_device_video_file(name, file_id)` returns `file` metadata: `id`,
  `gallery_id`, `kind`, `title`, `filename`, `bytes`, `metadata` and `created_at`.
- `delete_device_video_file(name, file_id)` deletes one file. Active content
  returns HTTP 409; the service does not stop playback implicitly.
- `upload_device_video_file(name, gallery_id, video_base64, filename="video.mp4",
  kind="video", title=null, play=false, repeat=1, presentation="auto")` uploads
  prepared bytes. Each file belongs to one gallery. `kind="video"` uploads prepared
  MP4; `kind="overlay"` uploads a prepared `.mvo` bundle. Set `filename` to the
  corresponding basename, such as `clip.mp4` or `overlay.mvo`.
- `download_device_video_file(name, file_id)` returns prepared bytes as
  `video_base64` and `size_bytes`.

MCP uploads and downloads are bounded to **16 MiB decoded bytes**. Prefer binary
REST upload/download for larger media; do not split one media file across tool
calls. An upload does not play unless `play=true`. Normal successful uploads
return the `file` metadata; when playing, results also include playback/layer
state. Deletions return `deleted` with the removed id.

### `play_device_video(name, url=null, file_id=null, gallery_id=null, repeat=1, presentation="auto")`

Supply exactly one nonempty `url`, `file_id` or `gallery_id`. Fullscreen URLs accept HTTP(S)/HLS or RTSP(S) media streams;
transparent prepared-alpha URLs require HTTP(S). `repeat=1` plays once (the default), `repeat=0` repeats forever and
`repeat=N` plays N full cycles. A gallery cycle includes its entire playlist.
For truly live sources, playback lasts until end-of-stream or stop and repeat is
irrelevant. `presentation` is `fullscreen`, `overlay` or `auto`.

Fullscreen restores the original gallery/item after completion, error or stop.
Overlay leaves the native gallery running beneath it. `auto` lets the service
choose based on the prepared media and capabilities.

### `stop_device_video(name, presentation="all")`

Stop both layers (`all`, the default), only `fullscreen`, or only `overlay`.
Stopping a layer leaves the other layer alone.

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
| `on_device_api_required` | device configuration does not support the requested feature |

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
GET    /devices/{name}/time
GET    /devices/{name}/galleries
POST   /devices/{name}/galleries     <- {"name": "Gallery name"}
GET    /devices/{name}/galleries/{gallery_id}
POST   /devices/{name}/galleries/{gallery_id}/items <- raw image bytes
POST   /devices/{name}/galleries/{gallery_id}/show
DELETE /devices/{name}/galleries/{gallery_id}
POST   /devices/{name}/items/{item_id}/show
DELETE /devices/{name}/items/{item_id}
POST   /devices/{name}/picture       <- {"path": "/frame/local/image.jpg", "timeout": 600}
GET    /devices/{name}/settings      <- include_sensitive=true only for raw secrets
GET    /devices/{name}/settings/schema
POST   /devices/{name}/settings      <- {"column": "value"}
POST   /devices/{name}/action        <- {"action": "resume", "value": null, "value2": null}
GET    /devices/{name}/video                 -> capabilities/layers/storage
POST   /devices/{name}/video                 <- JSON: exactly one url/file_id/gallery_id, repeat, presentation
POST   /devices/{name}/video/stop            <- {"presentation": "all"}
GET    /devices/{name}/video/storage
GET    /devices/{name}/video/galleries
POST   /devices/{name}/video/galleries       <- {"name": "Video gallery"}
GET    /devices/{name}/video/galleries/{gallery_id}
DELETE /devices/{name}/video/galleries/{gallery_id}
POST   /devices/{name}/video/galleries/{gallery_id}/files <- raw prepared bytes
GET    /devices/{name}/video/files
GET    /devices/{name}/video/files/{file_id}
DELETE /devices/{name}/video/files/{file_id}
GET    /devices/{name}/video/files/{file_id}/data -> raw prepared bytes
GET    /status                        -> device counts
GET    /healthz
```

```bash
curl -X PUT http://127.0.0.1:8733/devices/canvas-1/image \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: image/jpeg" \
  --data-binary @poster.jpg
```

Binary video upload uses `Content-Type: application/octet-stream` and requires
`Content-Length`. Query arguments are `filename`, `kind`, `title`, `play`,
`repeat` and `presentation` with the same defaults as the MCP tool. Storage is
checked before the body is accepted. There is no fixed REST media-size cap;
available device and temporary service storage govern admission. Binary download
returns `application/octet-stream`. Every video route requires authentication.

```bash
curl -X POST 'http://127.0.0.1:8733/devices/canvas-1/video/galleries/GALLERY_ID/files?filename=clip.mp4' \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/octet-stream" \
  --data-binary @clip.mp4
```

Device video errors preserve HTTP status and `is_ok=false`: 400 validation,
403 access denied, 404 missing content, 409 active content and 507 insufficient
space. Service request validation returns 422, authentication 401, and a missing
upload length 411. Failed video tool results may include `http_status` alongside
the original device error. `video_base64_invalid`, `video_too_large` and
`video_size_invalid` indicate MCP upload validation errors. A download that
exceeds the MCP limit fails with `invalid_request`; use the binary REST route.

## Notes

- Images are stored server-side, so a frame keeps showing the last image after a
  reboot without the agent running.
- `set_device_image_data` fails if the image cannot be decoded or the orientation is
  wrong; nothing is stored on failure.
- Only one image per device is held. Setting another replaces it.

Prepared fullscreen streams accept HTTP(S)/HLS and RTSP(S) media URLs. The native
decoder must support the actual stream codec/profile; protocol availability is
not playback proof. Transparent prepared-alpha streams require HTTP(S).
