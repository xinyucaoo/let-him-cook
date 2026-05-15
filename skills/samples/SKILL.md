---
name: samples
description: List the available cooking sound samples in the let-him-cook pool.
---

Run:

```
PORT=$(cat ~/.cache/let-him-cook/port 2>/dev/null) && [ -n "$PORT" ] && curl -s "http://127.0.0.1:${PORT}/samples"
```

Report the JSON list of sample basenames to the user. These are the samples that may be randomly selected each turn. If `$PORT` was empty, tell the user the let-him-cook bridge isn't running.
