---
name: unlock
description: Release the let-him-cook session lock so any Claude Code session's hooks can play audio again.
---

Run:

```
PORT=$(cat ~/.cache/let-him-cook/port 2>/dev/null) && [ -n "$PORT" ] && curl -s "http://127.0.0.1:${PORT}/unlock"
```

Confirm to the user that audio is no longer scoped to a single session — any session's hooks will now play sounds. If `$PORT` was empty, the bridge isn't running.
