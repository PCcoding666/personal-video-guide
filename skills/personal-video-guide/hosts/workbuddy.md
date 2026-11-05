# WorkBuddy

Checked against WorkBuddy 5.7.6 on 2026-11-01. A second pass on a signed-in machine is still open.

- Copy this skill folder to `~/.workbuddy/skills/personal-video-guide/`.
- Point WorkBuddy at the stdio server in `~/.workbuddy-ai/mcp.json`:

```json
{
  "mcpServers": {
    "video-guide": {
      "command": "video-guide",
      "args": ["mcp"]
    }
  }
}
```

- Restart WorkBuddy and trust the connector once under Connectors → Custom connectors.
- Create a scheduled task only after the user agrees to the time. The app has to be running then, or the task waits until the next open. Pause and cancel on the Scheduled page.
- Install `personal-video-guide[mcp]` in the same environment as the `video-guide` command on `PATH`.

The watch-along method is the same as on Codex. Links still come only from `link`.
