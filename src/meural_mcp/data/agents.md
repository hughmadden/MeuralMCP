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
orientation, its current gallery, and the on-device gallery state. `current_mode`
is the active display owner; `modes` lists registered IDs/labels/icons/enabled
flags. `controls` reports menu kind, pending candidate, generation and event
cursor. A highlighted `pending_mode` is not the current owner. Unavailable mode
state is null/empty rather than inferred.

### `get_device_image(name)`

The stored image path for a device, and whether one exists.

### `set_device_image_data(name, image_base64, filename)`

Store image bytes and automatically publish them. The service creates/reuses a
managed one-item gallery, uploads the new item, removes stale managed-gallery
items, and selects that gallery and picture. These are extra calls made by the
service; the caller does not need separate publish/show calls after success while Gallery
is active.
Use this over remote transport, where the server cannot see your filesystem.

This changes the native gallery/picture, **not the active display mode**. If
another mode owns the screen, it can remain visible. For immediate display, call
`select_device_mode(name, "gallery")` **before** uploading/publishing. Gallery
restores its saved native selection on activation, which can overwrite a selection
made while it was inactive. If already uploaded while away, select Gallery and
then call `publish_device` to re-publish the stored bytes.

```json
{"name": "canvas-1", "filename": "poster.jpg", "image_base64": "<base64>"}
```

### `clear_device_image(name)`

Forget the stored image for a device. The frame keeps showing whatever it has.

### `publish_device(name)`

Re-send the stored image to the frame, replacing stale managed-gallery items and
selecting gallery/picture automatically. This does not change the active mode;
select Gallery first when it should own the screen.
Images are delivered once, so use this if a frame has been reset or is showing
the wrong thing.

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

Add base64 image bytes to an on-device gallery and return its item ID. This
operation does not select the gallery/item or change the active mode. Use
`show_device_gallery` / `show_device_item` as explicit follow-up calls. Select
Gallery **before** those show calls when it should take display ownership.

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

## Modes and menu input

Gallery is built in and is the initial mode. External modes register data-only
nested menus and consume events; the controller does not run their code.

- `get_device_modes(name)` lists complete definitions, current `controls`,
  and timeout overrides. Reading or registering never activates a mode.
- `select_device_mode(name, mode_id)` explicitly commits a mode switch now and
  closes the menu/picker. API/MCP callers can switch modes directly.
- `navigate_device_menu(name, direction)` accepts `left`, `right`, `up`, `down`.
  With one enabled mode, closed-menu left/right does nothing; up opens its menu.
  With multiple modes, left/right opens/browses a picker while the current mode
  keeps running. Up selects; down or timeout cancels. Each root menu includes
  Switch mode… only when multiple modes are enabled. Inside mode menus,
  left/right highlights, up enters/selects, and down backs out.
- `register_device_mode(name, definition)` registers/replaces an external mode
  without restart. Supply a complete object with `id`, `label`, `menu`, optional
  `icon`, `enabled` (default true), and root `idle_timeout_s` (default 12).
  On supporting frames, optional `menu_renderer` defaults to `x11`. Use
  `menu_renderer="external"` only when the mode's presenter consumes and composes
  the on-device menu surface; it then receives menu pixels instead of a separate
  visible X11 menu. The picker follows the active owner's renderer while browsing
  other modes. The local surface contract is in the device's `/llms.txt`.
  Branches have `id`, `label`, `children` and optional icon/idle timeout; leaves
  have `id`, `label`, `action`, optional icon and scalar `value`. IDs use ASCII
  letters/digits/dot/underscore/colon/hyphen, start with a letter/digit, max 128;
  node IDs are unique per mode. Labels max 160 characters without controls;
  string values max 512. Icons are installed theme names, never URLs/paths.
  Bounds: 16 modes including Gallery, 64 nodes per external mode, depth 8,
  64 KiB control body. Gallery is protected and `events` is reserved.
- `unregister_device_mode(name, mode_id)` removes an inactive external mode.
  Active modes must first be left; built-in Gallery cannot be removed.
- `get_device_mode_events(name, after=0, epoch=null)` polls activation,
  deactivation and opaque leaf-action events. Keep a cursor per server epoch,
  deduplicate `(epoch, sequence)` and reconcile current owner/generation after
  startup, reconnect or `gap=true`. The 128-event feed is RAM-only; it is not a
  durable work queue. Ignore stale-generation actions. Mode implementations
  perform their own rendering/actions; selection acceptance is not proof of
  renderer readiness. Browse/cancel emits no ownership events.
- `set_device_menu_timeout(name, mode_id, seconds, menu_id=null)` sets a persistent
  idle override immediately. Seconds is integer 1–3600 or null to clear it.
  Omit menu_id for root/picker; supply a branch ID for a submenu. Unset submenus
  inherit the nearest parent. Navigation resets the deadline. A closed/expired
  menu retains no GUI/native menu renderer and does not stop deliberate media.

Status mode summaries preserve the reported `menu_renderer`. A null value means
an older frame did not advertise it; do not infer composition support from that.

```json
{"name":"canvas-1","definition":{"id":"reef","label":"Reef","icon":"applications-multimedia","menu":[{"id":"feed","label":"Feed fish","action":"feed"}]}}
```

Then explicitly select with `{"name":"canvas-1","mode_id":"reef"}`.
The same direct selection is available through `run_device_action` using
`action="select_mode"`, `value="reef"`; `action="navigate"` sends menu input.

## Prepared video and streams

Video tools preserve prepared media in named galleries. Inspect
`get_device_video(name)` before choosing a presentation: it returns capabilities,
`layers.fullscreen`, `layers.overlay`, compatibility playback status and storage.
`get_device_video_storage(name)` returns `storage` with `total_bytes`, `free_bytes`,
`reserve_bytes`, `available_upload_bytes`, `used_bytes` and `file_count`.

Prepare media before uploading. Normal video uses MP4 with H.264 Baseline and
YUV420P pixels at the intended display size/orientation. Transparent overlays use
prepared `.mvo` bundles (media type `application/vnd.meural.overlay`), with true
per-pixel alpha at the native canvas dimensions. Check advertised
`capabilities.overlay.formats` and `preferred_format`: MeuralDeviceControl v1.3.0
adds sparse `MVOZ0002` for small moving effects while accepting legacy
`MVOZ0001`. Prepare or losslessly repack effects upstream for the preferred
format. The service forwards prepared bytes unchanged; all encoding, resizing,
rotation and alpha preparation happen upstream. The frame decodes and displays
the result. Sparse layer status identifies the actual renderer in `backend`,
successful content blends in `hardware_jobs`, and active hardware failure in
`hardware_failed`; inspect these alongside frame/cycle progress. See the operator
preparation guide for the host preparation tool and the device's `/llms.txt`
for format details.

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
GET    /devices/{name}/modes
POST   /devices/{name}/modes                 <- complete definition object
GET    /devices/{name}/modes/events          ?after=0&epoch=...
DELETE /devices/{name}/modes/{mode_id}
POST   /devices/{name}/modes/{mode_id}/select
POST   /devices/{name}/modes/{mode_id}/timeout <- {"seconds":12,"menu_id":null}
POST   /devices/{name}/menu                  <- {"direction":"right"}
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

## Loading and progress feedback

Available with DeviceControl v1.3.1 and MeuralMCP v0.7.2. No mode registration
flag or restart is needed to use feedback. The mode client reports loading; the
menu service draws it. It does not measure your work or infer readiness.

### What the client should do

1. Register and consume mode events normally. Reconcile `controls.active_mode`
   before working. A user/controller can explicitly call `select_device_mode`;
   a mode worker must not keep selecting itself on startup, reconnect or update.
2. When its mode becomes active, freeze `controls.epoch` and
   `controls.mode_generation` for that job. For a long menu action, freeze them
   at action start. Use the current controls snapshot, not an old queued event.
   Also track a local job identity to suppress superseded actions.
3. Call `set_device_menu_progress` to show a message and animated wait bar, or
   measured percentage. Keep the mode's presentation loop responsive and able
   to display the menu during preparation, including before the final scene is
   ready. A loading request alone cannot start a mode's renderer.
4. For long work, renew before timeout (for example every 10 seconds for a
   30-second lifetime), while continuing to poll events/status. Renew only while
   that same job/session is current and its feedback remains visible.
5. Present the first usable frame, or finish applying the action, then explicitly
   clear feedback. Download/decode completion and a reported 100 percent do not
   clear it or prove presentation. Completion never stops playback or changes
   modes. A late clear leaves an ordinary menu open.
6. Stop the old job's updates/completion on deactivation, session change, or 409.
   Reconcile and handle a new activation separately. Never attach a fresh epoch
   or generation to an old job merely to retry it.

A mode switch shows automatic “Loading…” feedback. Built-in activation clears
on return; external modes have a 15-second fallback. Clients should replace or
clear it promptly. Client updates have their own bounded lifetime, default 30
seconds. Timeout/dismissal hides feedback without cancelling work or claiming
that loading succeeded.

### MCP calls

Read `get_device_modes(name="canvas-1")` or `get_device_status(name="canvas-1")`
and take the frozen session from the returned `controls`. If a caller explicitly
uses `select_device_mode`, its result also contains the new `controls` session.
The following are tool arguments; replace the example epoch/generation with
those actual values and use the ID of your registered, active mode.

Start indeterminate feedback with `set_device_menu_progress`:

```json
{"name":"canvas-1","mode_id":"sample","progress":{"epoch":"<controls.epoch>","mode_generation":3,"message":"Loading scene…","percent":null,"timeout_s":30}}
```

Update that same job with a measured checkpoint:

```json
{"name":"canvas-1","mode_id":"sample","progress":{"epoch":"<controls.epoch>","mode_generation":3,"message":"Loading textures…","percent":65,"timeout_s":30}}
```

After actual presentation/completion, clear using only the frozen session and
completion flag. Do not carry message/percentage/timeout fields into this call:

```json
{"name":"canvas-1","mode_id":"sample","progress":{"epoch":"<controls.epoch>","mode_generation":3,"clear":true}}
```

The equivalent REST route is
`POST /devices/{name}/modes/{mode_id}/progress`; its JSON body is just the inner
`progress` object above, not the tool's name/mode/progress wrapper. Both surfaces
return current `controls`. Inspect the operation's `status` and any `http_status`;
a successful MCP transport response alone does not prove frame acceptance.

### Inputs, renewal and recovery

- `epoch` and integer `mode_generation` are required on every call, including
  completion. They must match the currently active mode session.
- Optional `message`: 1–160 characters without control codes; default “Please
  wait…”. Optional `percent`: null/omitted for animated waiting, or a finite
  number 0–100. Use measured progress; choose indeterminate when it is unknown.
- Optional `timeout_s`: integer 1–3600, default 30. Each update renews the
  indicator's lifetime, even if values are unchanged. `clear=true` accepts only
  epoch/generation alongside it. A percentage of 100 still needs explicit clear.
- Status `controls.progress` is null or message/percent/timeout/source, with
  `menu_kind=progress` while visible. After the initial update, if a later
  snapshot has no progress or another menu kind, stop renewal for that job.
  Another update would reopen feedback and replace the user's menu. There is
  no persistent dismissal latch; a swipe does not cancel the underlying work.
- Check your local job identity before every update/clear. A mode generation
  distinguishes activations, not actions within one activation. Old job A must
  not clear newer job B's indicator. Serialize jobs or suppress superseded ones.
- Inactive/stale sessions return 409. A 404 can mean an unknown mode or an older
  frame without this endpoint; inspect its catalog/API version. Do not emulate
  readiness, repeatedly register, or switch modes as a fallback. Invalid REST
  inputs return 422; native frame validation errors may be forwarded as 400.
- On failure, stop renewal and clear only if it is still your current job/session,
  then report the error through the mode's own UI/logs. No error/ready/job-ID
  field is accepted. Hidden feedback also occurs after dismissal/timeout, so it
  must never be interpreted as successful loading or a readiness acknowledgement.
