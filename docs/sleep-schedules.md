# Sleep Schedules

Optional per-device windows in which a frame is suspended. Set them over MCP or
in config; the daemon applies them at the edges.

```
set_device_schedule(name, sleep_start, wake_time, timezone, enabled)
```

`sleep_start` and `wake_time` are `HH:MM` in an IANA timezone. A window that
crosses midnight sets `sleep_start` later than `wake_time`.

```json
{"name": "canvas-1", "sleep_start": "22:30", "wake_time": "07:00",
 "timezone": "Etc/UTC", "enabled": true}
```

Config equivalent, on the device entry:

```json
"schedule": {
  "enabled": true,
  "timezone": "Etc/UTC",
  "sleep_start": "22:30",
  "wake_time": "07:00"
}
```

Behaviour:

- Evaluated by `meural-mcp daemon` only. Without the daemon a schedule does
  nothing.
- The daemon suspends at the start of the window and wakes at the end, once per
  edge; restarts do not re-apply an unchanged state.
- `get_device_schedule` returns the current window, `clear_device_schedule`
  removes it.
- Times are validated: equal start and end, malformed times and unknown timezones
  are rejected with a reason.
