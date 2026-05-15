---
name: reset
description: Silence the bed and rewind the active sample to frame 0 — useful in fixed mode to start the sample from the top on the next turn.
---

Run:

```
PORT=$(cat ~/.cache/let-him-cook/port 2>/dev/null) && [ -n "$PORT" ] && curl -s "http://127.0.0.1:${PORT}/reset"
```

Confirm the bed has been silenced and rewound. If `$PORT` was empty, tell the user the let-him-cook bridge isn't running.
