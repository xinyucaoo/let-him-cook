---
name: fixed
description: Lock let-him-cook to a single sample. The user passes the sample basename as the skill argument (e.g. frying_pan_2.mp3).
---

The user's argument is the sample basename. URL-encode it if it contains spaces or special characters, then run:

```
curl -s 'http://127.0.0.1:8766/mode/fixed?sample=<BASENAME>'
```

…substituting `<BASENAME>` for the argument. Report the response to the user. If the response is a 404, call the `samples` skill to list available samples and suggest the user pick one of those.
