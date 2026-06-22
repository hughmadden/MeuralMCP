# MeuralMCP

Small LAN-first daemon, REST API, and MCP server for keeping Meural Canvas
previews loaded.

## Features

- Quiet daemon loop that retries often-failing frames without noisy logs.
- REST API protected by a shared token.
- Hosted remote MCP endpoint for coding agents, protected by the same shared token.
- Optional stdio MCP adapter for clients that cannot connect to remote MCP directly.
- Per-device current image storage.
- Orientation validation before writing a preview.
- In-memory/status tracking for LAN reachability failures.
- Optional cloud init that creates/assigns one-item blank galleries for landscape
  and portrait devices, sets hold durations, and syncs configured cloud device IDs.

## Credits

This project builds on public Meural local-control work from the community:

- [Guy Sie's HA-meural](https://github.com/GuySie/ha-meural), introduced on the
  [Home Assistant forum](https://community.home-assistant.io/t/ha-meural-custom-integration-for-netgear-meural-canvas-digital-art-frames/200008),
  demonstrated that Meural Canvas devices expose both a cloud API and a local
  interface suitable for Home Assistant control.
- [NETGEAR's support article](https://kb.netgear.com/000060746/Can-I-control-the-Canvas-without-a-mobile-app-or-gesture-control-and-if-so-how)
  documents the built-in browser-accessible `/remote` controller on the same LAN.
- [bigboxer23/meural-control](https://github.com/bigboxer23/meural-control)
  documented the practical issue with transient preview/postcard display and the
  more reliable single-playlist/gallery pattern.

## Quick Start

Most users should run cloud init first. It is the step that discovers your
frames, writes the starter config, changes Meural cloud timeout settings, and
assigns the neutral blank gallery used by the local preview manager.

Cloud init changes these Meural cloud values:

- Sets `imageDuration=86400`, `previewDuration=86400`, and `overlayDuration=120`
  on discovered devices.
- Finds or creates one landscape and/or one portrait blank gallery.
- Uploads one blank image to each blank gallery if the gallery is empty.
- Assigns each device to the matching blank gallery for its orientation.
- Syncs the devices after changing these settings.

Cloud init does not delete your existing Meural images or galleries, but it can
change which gallery is assigned to each discovered device. Review the generated
`config.json` after init and edit device names, IPs, orientations, or enabled
flags before starting the daemon.

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e .

meural-mcp --storage-dir ~/.config/meural-mcp init-cloud \
  --username "$MEURAL_USERNAME" \
  --password "$MEURAL_PASSWORD"
```

Package dependencies are declared in `pyproject.toml`; `pip install -e .` installs
the CLI plus bounded runtime dependencies for FastAPI, Uvicorn, Requests, and MCP.

`llms.txt` is included as a concise map for coding agents and documentation
ingestion. `AGENTS.md` contains contributor guardrails for keeping this repo
public-safe.

If you do not want cloud init, `init-local` can create an empty generic config:

```bash
meural-mcp --storage-dir ~/.config/meural-mcp init-local --api-token "change-me"
```

With `init-local`, you must manually fill in device names, cloud IDs, LAN IPs,
orientations, and enabled flags in `~/.config/meural-mcp/config.json`. It also
does not set timeout values or blank galleries in Meural cloud.

Run the API:

```bash
meural-mcp --storage-dir ~/.config/meural-mcp serve --host 127.0.0.1 --port 8733
```

Keep the REST API bound to localhost where possible. If you want to expose it to
other machines, put nginx or Caddy in front of it and terminate HTTPS there
rather than binding the API directly to the LAN as plain HTTP.

Check summary status:

```bash
curl http://127.0.0.1:8733/status \
  -H "Authorization: Bearer change-me"
```

Run the daemon:

```bash
meural-mcp --storage-dir ~/.config/meural-mcp daemon
```

## Services and HTTPS

Sample service and reverse-proxy files are provided in `examples/`:

- `examples/systemd/meural-mcp-daemon.service`
- `examples/systemd/meural-mcp-api.service`
- `examples/systemd/meural-mcp-user.service`
- `examples/systemd/meural-mcp-api-user.service`
- `examples/reverse-proxy/Caddyfile`
- `examples/reverse-proxy/nginx.conf`

`init-cloud` is a one-shot CLI setup command. Run it manually so you can see and
approve the cloud changes. The daemon and API are separate long-running
processes: the daemon keeps previews loaded, and the API serves local status and
image writes. In the system service examples, both long-running services use
`/var/lib/meural-mcp` for config/state and expect the project installed at
`/opt/meural-mcp/.venv/bin/meural-mcp`.

For a system install:

```bash
sudo useradd --system --home /var/lib/meural-mcp --create-home --shell /usr/sbin/nologin meural-mcp
sudo install -d -o meural-mcp -g meural-mcp /var/lib/meural-mcp
sudo cp examples/systemd/meural-mcp-*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now meural-mcp-daemon.service meural-mcp-api.service
```

For a user install:

```bash
mkdir -p ~/.config/systemd/user
cp examples/systemd/meural-mcp-user.service ~/.config/systemd/user/meural-mcp.service
cp examples/systemd/meural-mcp-api-user.service ~/.config/systemd/user/meural-mcp-api.service
systemctl --user daemon-reload
systemctl --user enable --now meural-mcp.service meural-mcp-api.service
```

The user service samples expect `meural-mcp` at `~/.local/bin/meural-mcp`.
Adjust `ExecStart` if you installed into a project virtual environment instead.

For LAN access, keep `meural-mcp-api.service` on `127.0.0.1:8733` and expose it
through HTTPS with Caddy or nginx. The shared token is still required by
MeuralMCP, but TLS should protect the token in transit.

The API service also exposes a remote streamable-HTTP MCP endpoint at `/mcp/`.
When the API is behind HTTPS, coding agents that support remote MCP should
connect directly to that URL with the same bearer token used by the REST API.
Clients do not need the MeuralMCP package installed just to use this hosted MCP
endpoint.

Set `MEURAL_MCP_ALLOWED_HOSTS` on the API service to the HTTPS hostnames clients
will use for remote MCP, such as `meural-mcp.example.test` or
`meural-mcp.example.test:443`. This keeps the MCP transport's DNS-rebinding
protection enabled while allowing your reverse-proxy hostname.

Example remote MCP URL:

```text
https://meural-mcp.example.test/mcp/
```

Run the stdio MCP adapter only for coding agents that do not support remote MCP:

```bash
meural-mcp --storage-dir ~/.config/meural-mcp mcp --transport stdio
```

If this compatibility adapter runs on your workstation while the daemon/API run
on another host, point it at the remote API instead. In this mode,
`set_device_image` reads the image path from the workstation and uploads the
bytes to MeuralMCP:

```bash
export MEURAL_MCP_API_URL="https://meural-mcp.example.test"
export MEURAL_MCP_API_TOKEN="..."
export MEURAL_MCP_API_VERIFY_TLS=false  # only for self-signed lab certs
meural-mcp mcp --transport stdio
```

MCP tools include:

- `list_devices`
- `get_device_status`
- `get_device_image`
- `set_device_image`
- `set_device_image_data`

`list_devices` and `get_device_status` return the configured device name,
display name, cloud ID, LAN IP, orientation, enabled flag, reachability, current
assigned image, and the last recorded state for each device.

For hosted remote MCP, prefer `set_device_image_data`, which accepts base64 image
bytes plus a filename. `set_device_image` accepts a path that is local to the
machine running the MCP server, so it is mainly useful for stdio adapters or
server-local files.

Both image-writing tools store the image only after validating orientation and
successfully loading the preview to the device. They return a failed result if
the image cannot be parsed/thumbnailed, the orientation is wrong, the device is
unknown, the file is missing, or the preview write fails.

Upload an image:

```bash
curl -X PUT http://127.0.0.1:8733/devices/canvas-1/image \
  -H "Authorization: Bearer change-me" \
  -H "Content-Type: image/jpeg" \
  --data-binary @image.jpg
```

## Cloud Init Details

Cloud init requires a Meural username/password.

```bash
meural-mcp --storage-dir ~/.config/meural-mcp init-cloud \
  --username "$MEURAL_USERNAME" \
  --password "$MEURAL_PASSWORD"
```

On a Linux host with user systemd available, add `--install-systemd` to write,
enable, and start `meural-mcp.service`. Interactive runs prompt for this unless
`--no-systemd-prompt` is supplied.

`init-cloud`:

1. Authenticates to Meural cloud with the supplied username/password.
2. Discovers cloud device IDs, names, LAN IPs, and writes them to `config.json`.
3. Generates a shared API token, writes it to `config.json`, and prints it so you can save it for REST/MCP clients.
4. If `config.json` already exists, writes a timestamped backup first.
5. Finds or creates landscape and/or portrait blank galleries, depending on the discovered devices.
6. Ensures each blank gallery has a single correctly oriented blank image if the gallery is empty.
7. Assigns each configured device to the matching blank gallery for its orientation.
8. Sets `imageDuration=86400`, `previewDuration=86400`, `overlayDuration=120`.
9. Syncs configured devices.

After init, review and edit `config.json` if you need to adjust device names,
cloud IDs, LAN IPs, orientations, or enabled flags before running the daemon.

## FAQ

### Why does this poll the devices?

Polling is not elegant, but it is deliberate. Meural frames can lose the loaded
preview after sleep, boot, or other internal resets, so the daemon checks
periodically and reloads the assigned preview when needed.

### Why does setup use a blank image/gallery?

The blank image is a holding pattern. It keeps the device on a single neutral
gallery by default, which makes the transition to locally managed previews a
little less awkward than leaving whatever cloud playlist happened to be active.

### Can I block the frames from the internet after setup?

Probably, but this still needs testing. Once cloud init has discovered devices,
set the timeout values, and assigned the blank galleries, users may be able to
firewall the Meural devices from the internet so future Meural cloud changes
cannot alter or disturb them.

### What would make this better?

A better solution likely needs hardware-level investigation. A community
teardown/hacking post describes a Meural RK3288 board and locating `G`, `R`, and
`T` test pads for ground, RX, and TX serial console access. Someone needs to
open a device, attach to those serial pins, and see whether there is a cleaner
local-control path than periodic preview reloads.
