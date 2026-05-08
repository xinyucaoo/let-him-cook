# let-him-cook

![let-him-cook](lethimcook.png)

Plays cooking sounds while Claude is working. Random sample on each turn — pan-frying, kettle whistling, microwave (with start beep + running loop + end beeps), chopping, and more.

## Install

In Claude Code:

```
/plugin install <owner>/let-him-cook
```

That's it. On first activation the plugin auto-creates a local Python venv and installs three deps (`numpy`, `sounddevice`, `soundfile`). All three ship with their native libraries bundled in their wheels — no `brew install` needed on macOS, no `apt` needed on most Linux. First run takes ~10s; subsequent activations are instant.

## How it works

When the plugin is active:
- Every prompt you submit triggers a random cooking sound from the pool to play during Claude's response.
- The sound stops when Claude finishes (or when you interrupt).
- Some samples have associated intro/outro one-shots (e.g. the microwave plays `start beep → running loop → end beep`).

## Slash commands

| Command | Effect |
|---|---|
| `/let-him-cook:samples` | List the available samples |
| `/let-him-cook:random` | Each turn picks a random sample (default) |
| `/let-him-cook:fixed <name>` | Lock to one sample, e.g. `frying_pan_2.mp3` |
| `/let-him-cook:reset` | Silence the bed and rewind to frame 0 |

## Adding your own sounds

Drop any `.mp3` / `.wav` / `.aiff` / `.flac` / `.ogg` into the plugin's `samples/` directory and reactivate. To pair an intro and outro one-shot to a bed (like the microwave), add an entry to `samples/chimes.json`:

```json
{
  "my_sample.mp3": {
    "intro": "intros/my_intro.wav",
    "outro": "chimes/my_outro.wav"
  }
}
```

## Sample credits

Cooking samples sourced from [BigSoundBank](https://bigsoundbank.com) (CC0) and [Pixabay](https://pixabay.com/sound-effects) (Pixabay license).
