# MeuralMCP

Local control of Netgear Meural Canvas frames. Publish images to a frame from a
CLI, a REST API, or an MCP server, without the mobile app and without Netgear in
the loop.

- **On-device API** — each frame is driven through the API it serves itself
  (`:8099`), authenticated with a per-device token. No DNS changes, no
  certificate, no man-in-the-middle.
- **Full local control** — galleries/items, immediate pictures, device time,
  settings/schema and hardware/network actions through REST and MCP.
- **REST API** — token-protected, loopback by default.
- **MCP server** — stdio or hosted streamable-HTTP, so coding agents can set
  images directly.
- **Durable** — images are stored server-side and re-served; a frame keeps its
  image across reboots with no agent running.
- **Orientation-aware** — landscape and portrait are validated before anything is
  written.

## Requirements

- A host that can reach the frames on the LAN.
- Each frame running its on-device API, with its token in the config. One
  command per frame: `ssh root@FRAME "sh /root/uf.sh token"`.

No DNS, certificate or firewall work. `docs/deploy.md` keeps the older
certificate/DNS path for estates that pre-date the on-device API.

## Quick start

```sh
pip install .
meural-mcp init
meural-mcp add-device --name canvas-1 --ip 192.0.2.10 --orientation landscape \
  --token "$(ssh root@192.0.2.10 'sh /root/uf.sh token')"
```

A device added without `--token` falls back to the frame's unauthenticated
`/remote` API on port 80.

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

Setting an image uploads it into a per-device gallery on the frame and switches
that frame to it — one image per device, managed by name. The service stores the
image server-side too, so re-publishing and reboots work with no agent running.
Nothing is proxied to Netgear.

With a token the work happens over the frame's own API (`:8099`); the frame
holds the file itself and no polling or command queue is involved.

## Sleep schedules

Optional per-device suspend windows, set over MCP (`set_device_schedule`) or
config. See [docs/sleep-schedules.md](docs/sleep-schedules.md).

## Caveats

- The token is a full-control credential in the config; `chmod 600` it and treat
  it like the API token.
- Frames must run the on-device API; a stock frame answers only `/remote`.
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
