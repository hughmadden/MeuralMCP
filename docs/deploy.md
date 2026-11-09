# Deploying MeuralMCP

Operator guide. Covers the certificate, isolating the frames, DNS, and the
service. Agent-facing method docs are in [agents.md](agents.md).

## What runs where

Two pieces:

1. **`meural-api`** — a small HTTPS service on port 443 that the frames talk to
   instead of Netgear. Installed by `service/meural-api-install`, runs as its own
   user, stores galleries under `/var/lib/meural-api`.
2. **`meural-mcp`** — this package: CLI, REST API and MCP server. It publishes
   images into `meural-api` and queues commands. It does not serve the frames.

Both belong on the same host, on the same layer 2 segment as the frames.

## 1. Certificate

The frames validate TLS against a frozen trust store, so `meural-api` must present
a certificate for **`api.meural.com`** that the frames already trust:

- **Subject/SAN:** `api.meural.com`
- **Format:** PEM chain — leaf first, then any intermediates. Key unencrypted PEM.
- **Place:** `/etc/meural-api/chain.pem` and `/etc/meural-api/key.pem`, owned
  `root:meural-api`, mode 640. The service runs as `meural-api` and must read
  both; `600 root:root` leaves it unable to start.

The frames do not check revocation, so a chain from an internal or lab CA works
provided the root is already in the frames' store. A chain that also covers
`*.api.meural.com` is fine but not required.

Paths are configurable in `/etc/meural-api/meural.conf` if you keep them
elsewhere.

Verify before going further, from another host:

```sh
openssl s_client -connect 192.0.2.5:443 -servername api.meural.com </dev/null 2>&1 | grep "Verify return code"
```

That must report `0 (ok)`. An endless `unknown_ca` usually means only the leaf is
being served — the chain file must contain the intermediates.

## 2. Isolate the frames

Frames must not reach Netgear, or the cloud can move them elsewhere.

- Give them their own network or DHCP scope where possible.
- Block their outbound internet at the router, or on this host:

```sh
nft add rule inet filter forward ip saddr <frame-subnet> oifname <wan> drop
```

- Let them reach the host running `meural-api`. If you also block forwarding
  between frame segments, explicitly accept traffic to this host's address, or
  the frames cannot load anything.

If the frames share a segment with the host, blocking their default gateway is
usually enough.

## 3. DNS

Point the frames' DNS at the host running `meural-api`, and make it answer
`api.meural.com` with its own address.

### Reconfiguring DNS

Use whatever serves DHCP for the frame scope. Set the DNS option for those leases
to this host's address on that segment, then force the frames to renew (reboot
them, or drop their leases). They keep the old value until they renew.

Two worked examples:

**dnsmasq** (the pattern this project was built on). Serve DNS only on the frame
interface and answer the name:

```conf
# /etc/dnsmasq.d/meural.conf
interface=eth1
bind-dynamic
address=/api.meural.com/192.0.2.5
```

Do not add a `dhcp-range` if your router hands out DHCP; dnsmasq only needs to
answer DNS. If dnsmasq already serves that scope, add the `address=` line to it
and skip the rest.

**Router DHCP.** Set the DNS server for the frame reservation or the frame VLAN to
the host address. Most consumer routers cannot set DNS per client, so a dedicated
VLAN or a dnsmasq/AdGuard/Pi-hole instance is usually the simple route.

Check from a client on the frame segment:

```sh
dig +short api.meural.com @192.0.2.5
```

It must return the host address. `tcpdump -n -i eth1 port 53` on the host should
show the frames asking.

Also blackhole NTP so the frames cannot drag their clock forward (see §5):

```conf
address=/ntp.ubuntu.com/#
address=/pool.ntp.org/#
```

## 4. Install

```sh
sudo sh service/meural-api-install
sudo systemctl enable --now meural-api
```

Then this package, as the `meural-mcp` user:

```sh
sudo -u meural-mcp python3 -m venv /opt/meural-mcp/.venv
sudo -u meural-mcp /opt/meural-mcp/.venv/bin/pip install /path/to/meural-mcp
```

Copy `examples/config.example.json` to
`/var/lib/meural-mcp/config.json`, then generate a token and register frames:

```sh
sudo -u meural-mcp meural-mcp --storage-dir /var/lib/meural-mcp init
sudo -u meural-mcp meural-mcp --storage-dir /var/lib/meural-mcp add-device \
  --name canvas-1 --ip 192.0.2.10 --orientation landscape
```

Install the API and daemon units from `examples/systemd/`, or run
`meural-mcp serve` and `meural-mcp daemon` by hand.

Keep the API on loopback; remote agents reach it through a reverse proxy. Working
samples are in `examples/reverse-proxy/` (nginx and Caddy). Both must disable
response buffering or the MCP response stream breaks, and both require
`MEURAL_MCP_ALLOWED_HOSTS=<public hostname>` on the service, because the transport
validates the `Host` header it is given.

## 5. Clock

A chain has a validity window, and the frame's clock decides whether it applies.
Outside the window the frame aborts with `certificate_expired` and stops polling.

`service/pin-clock.sh` sets a frame's clock over its plain-HTTP LAN API. Run it at
boot once frames are reachable, and keep NTP blackholed. List frame addresses in
`/var/lib/meural-api/frames.txt`; the script also picks up current LAN neighbours.

Set `PIN_LO` and `PIN_HI` to dates inside your certificate's validity window — the
defaults are examples only. The script keeps the clock advancing with real time
and freezes it at `PIN_HI`, so an expired chain never bites.

## 6. Sleep schedules

Optional per-device suspend windows are set over MCP (`set_device_schedule`) or
config and applied by `meural-mcp daemon` — so the daemon must run for them to
take effect. See [sleep-schedules.md](sleep-schedules.md).

## 7. Firmware and CA updates

Nothing proxies to Netgear, so updates are inert: the frames are told everything
is fine and nothing changes. Useful for stability, but it means a real firmware or
CA update cannot arrive. Reintroduce a proxy if you ever need one.

## 8. Verify

1. `s_client` reports `Verify return code: 0 (ok)`.
2. `dig` from the frame segment returns the host.
3. Frames appear in `/var/log/meural-api/access.log` with a product key.
4. Setting an image returns `pass` and the frame shows it.
5. `/var/lib/meural-api/status/<key>` shows the frame reporting the new gallery.

## 9. Troubleshooting

| Symptom | Likely cause |
| --- | --- |
| Frames never call the API | DNS still points at the router, or leases not renewed |
| `unknown_ca` alerts | chain file has no intermediates |
| Frames poll but images never appear | gallery rejected — orientation or checksum |
| Frame stops polling entirely | clock outside the certificate window |
| `download item failed` in `report_command_error` | wrong checksum or size in the manifest |
| Frame reverts to old art | old galleries still assigned on the Netgear account |
