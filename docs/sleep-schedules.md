# Sleep Schedules

MeuralMCP supports optional per-device sleep windows. A schedule is evaluated by
the daemon on every poll.

Example:

```json
{
  "sleep_schedules": [
    {
      "name": "bedtime",
      "enabled": true,
      "timezone": "Australia/Sydney",
      "sleep_start": "19:30",
      "wake_time": "07:00",
      "devices": ["canvas-1", "canvas-2"]
    }
  ]
}
```

Behavior:

- During the sleep window, matching reachable devices receive
  `/remote/control_command/suspend`.
- During the sleep window, the daemon does not write postcard previews or reload
  images for those devices.
- Outside the sleep window, matching reachable devices receive
  `/remote/control_command/resume` before normal image reload behavior.
- Windows may cross midnight. For example, `19:30` to `07:00` means sleep from
  7:30 PM until 7:00 AM in the configured timezone.
- Schedule device names match either `name` or `display_name`, normalized the
  same way as MCP/API device lookup.
