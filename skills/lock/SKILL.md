---
name: lock
description: Lock let-him-cook audio to this session only. Other Claude Code sessions' hooks become silent no-ops. Run /let-him-cook:unlock to revert.
---

Run:

```
PORT=$(cat ~/.cache/let-him-cook/port 2>/dev/null) && [ -n "$PORT" ] && curl -s "http://127.0.0.1:${PORT}/lock"
```

Report the response to the user. On success it returns `locked:<basename>` confirming which session is now the only one allowed to play audio. If the response is `no session to lock — try submitting a prompt first`, the bridge hasn't seen a UserPromptSubmit hook from this session yet — the user should try invoking the skill again (this very invocation should fix it for the next try).

If `$PORT` was empty, the bridge isn't running.
