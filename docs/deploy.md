# Deploying MeuralMCP

Operator guide for the on-device-only service. Agent-facing methods are in
[agents.md](agents.md). MeuralMCP drives each frame through its own
MeuralDeviceControl API on `:8099`; there is no queue, frame-facing replacement
cloud service, DNS override, certificate, or tokenless compatibility path.

## Requirements

- A host that can reach the frames on the LAN.
- Each frame running MeuralDeviceControl with its unique token in homepi's config.
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

The old frame-facing `meural-api` system service is not installed or used.

## Publishing and status

`POST /devices/<name>/image` stores the supplied image on homepi, uploads it into
a managed on-device gallery, removes stale items, and selects the gallery and new
item. `POST /devices/<name>/publish` repeats that operation without a new upload.
No command queue or periodic reapply service is needed.

Use `GET /devices` for inventory and `GET /devices/<name>` for current status.
`gallery_id` is `null` until the first direct publish. Galleries, settings,
actions, and immediate pictures use the routes in [agents.md](agents.md).

## Sleep schedules

Per-device suspend windows are set in config or over MCP
(`set_device_schedule`) and applied by `meural-mcp daemon`. See
[sleep-schedules.md](sleep-schedules.md).

## Security and verification

- Frame tokens are full-control credentials and plaintext on the LAN. Restrict
  access to the frame and homepi networks, keep config mode 600, and do not log
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
