# MeuralMCP

Local control of Netgear Meural Canvas frames. Publish images to a frame from a
CLI, a REST API, or an MCP server, without the mobile app and without Netgear in
the loop.

- **Local API** — a small HTTPS service answers the frames in place of the Meural
  cloud, so they load art from your own host.
- **REST API** — token-protected, loopback by default.
- **MCP server** — stdio or hosted streamable-HTTP, so coding agents can set
  images directly.
- **Durable** — images are stored server-side and re-served; a frame keeps its
  image across reboots with no agent running.
- **Orientation-aware** — landscape and portrait are validated before anything is
  written.

## Requirements

- Linux host on the same layer 2 segment as the frames.
- A TLS certificate the frames trust for `api.meural.com` (PEM chain, leaf first).
- DNS for the frames pointed at that host, and their internet blocked.

The certificate is the only fiddly prerequisite. See
[deploying](docs/deploy.md) for the certificate, DNS and firewall work.

## Quick start

```sh
pip install .
meural-mcp init
meural-mcp add-device --name canvas-1 --ip 192.0.2.10 --orientation landscape
```

Deploy the local API and point DNS at the host first — until then the frames have
nothing to talk to. Full steps: [docs/deploy.md](docs/deploy.md).

Run the API and daemon:

```sh
meural-mcp serve --host 127.0.0.1 --port 8733
meural-mcp daemon
```

Set an image:

```sh
curl -X PUT http://127.0.0.1:8733/devices/canvas-1/image \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: image/jpeg" \
  --data-binary @poster.jpg
```

## MCP

For agents, the tool surface and nothing else:
[docs/agents.md](docs/agents.md).

```sh
meural-mcp mcp --transport stdio
```

The REST API also hosts the same MCP tools at `/mcp/`, using the same bearer
token. Set `MEURAL_MCP_ALLOWED_HOSTS` to the HTTPS hostname clients will use so
the transport's DNS-rebinding protection stays enabled.

A stdio adapter on a workstation can drive a remote install:

```sh
export MEURAL_MCP_API_URL="https://meural-mcp.example.test"
export MEURAL_MCP_API_TOKEN="..."
meural-mcp mcp --transport stdio
```

## How it works

The frames poll `api.meural.com` for commands and galleries. With DNS pointed at
this host and a trusted certificate, they take commands from here instead.
Setting an image publishes a one-item gallery and queues it for that frame; the
frame fetches the image and holds it. Nothing is proxied to Netgear, so cloud
changes cannot move a frame.

## Sleep schedules

Optional per-device suspend windows, set over MCP (`set_device_schedule`) or
config. See [docs/sleep-schedules.md](docs/sleep-schedules.md).

## Caveats

- Certificate chains expire. Keep the frames' clocks correct and NTP blocked, or
  keep the chain long-lived — see [deploying](docs/deploy.md#clock).
- Firmware updates do not arrive on their own while the frames are isolated.
- One image per device is held.

## Credits

Built on public Meural local-control work:

- [Guy Sie's HA-meural](https://github.com/GuySie/ha-meural) established that
  frames expose a local interface suitable for automation.
- [NETGEAR's support article](https://kb.netgear.com/000060746/Can-I-control-the-Canvas-without-a-mobile-app-or-gesture-control-and-if-so-how)
  documents the `/remote` controller.
- [bigboxer23/meural-control](https://github.com/bigboxer23/meural-control)
  documented preview reliability problems and the gallery-hold pattern.
