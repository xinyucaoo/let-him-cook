---
name: fixed
description: Lock let-him-cook to a single sample. Pass the sample basename as $ARGUMENTS (e.g. frying_pan_2.mp3).
---

Run `curl -s 'http://127.0.0.1:8766/mode/fixed?sample=$ARGUMENTS'` and report the response. If the response is a 404, list the available samples by calling `/samples` and suggest the user pick one of those.
