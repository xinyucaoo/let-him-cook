---
name: random
description: Switch let-him-cook to random mode — each turn picks a different cooking sound.
---

Run:

```
PORT=$(cat ~/.cache/let-him-cook/port 2>/dev/null) && [ -n "$PORT" ] && curl -s "http://127.0.0.1:${PORT}/mode/random"
```

Confirm to the user that random mode is active. If `$PORT` was empty, the bridge isn't running — tell the user the let-him-cook bridge needs to be started first.
