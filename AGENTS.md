# Agent Notes

This repository is intended to be public. Keep it generic:

- Do not commit real device names, serials, MAC addresses, IPs, hostnames,
  certificate material or frame tokens.
- Do not commit credentials, API tokens, `.env` files, generated state or private
  images.
- Use `canvas-1`, `canvas-2` and `192.0.2.x` in examples.

## Audience split

Two kinds of reader, kept separate:

- **Agents using the service** — `docs/agents.md` only. It documents MCP tools and
  REST routes and deliberately avoids how the frames are driven, certificates,
  DNS and token handling.
- **Operators** — `README.md` and `docs/deploy.md`. Per-device tokens, install,
  and the legacy certificate/DNS path.

When changing the tool surface, update `docs/agents.md` and `llms.txt`. When
changing deployment, update `docs/deploy.md`. Do not leak operator detail into the
agent docs.

## Code layout

- `service/` — the HTTPS API the frames talk to, its unit and installer.
- `src/meural_mcp/manager.py` — device registry, image store, gallery publishing.
- `src/meural_mcp/mcp_server.py`, `api.py`, `cli.py` — surfaces over the manager.
- `examples/` — generic config and systemd samples.
