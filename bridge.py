#!/usr/bin/env python3
"""bridge.py — let-him-cook audio bridge.

Holds the audio output stream and tails the JSONL of whichever Claude Code
session most recently fired its UserPromptSubmit hook. Hooks pass the
transcript path as a query param so we tail one specific session — never
the latest-anywhere — which is how cross-session audio bleed is avoided.

HTTP control endpoints:

    GET /ignite?transcript=<path>     — start the bed, scope to one session
    GET /extinguish?transcript=<path> — stop the bed (matching session only)
    GET /reset                        — silence + rewind to frame 0
    GET /samples                      — JSON list of pool basenames
    GET /mode/random                  — random pick on each ignite
    GET /mode/fixed?sample=<basename> — lock to one sample
"""

import argparse
import asyncio
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, parse_qs

# Where the bridge advertises its currently-listening port. Hooks and skills
# read from this file via bin/hook-curl.sh (or inline cat) so the actual
# port can be anything — picking an ephemeral one means we coexist with
# whatever else may already be on the previously-hardcoded 8766.
PORT_FILE = Path.home() / ".cache" / "let-him-cook" / "port"

# Path to the transcript JSONL of the session currently driving audio.
# Set by /ignite, used by session_watcher_loop to know which file to tail.
# Protected by _active_lock for safe handoff between the HTTP thread and
# the watcher coroutine.
_active_transcript: Optional[Path] = None
_active_lock = threading.Lock()

_synth = None  # type: ignore[var-annotated]


def _set_active_transcript(path: Optional[Path]) -> None:
    global _active_transcript
    with _active_lock:
        _active_transcript = path


def _get_active_transcript() -> Optional[Path]:
    with _active_lock:
        return _active_transcript


def start_control_http(port: int = 0):
    class Handler(BaseHTTPRequestHandler):
        def _ok(self, body: bytes = b"OK", content_type: str = "text/plain"):
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            url = urlparse(self.path)
            path = url.path
            qs = parse_qs(url.query)
            if path == "/extinguish":
                # Scope: only extinguish when the Stop hook fires *for the
                # session that lit the bed*. Without this scoping, a Stop in
                # session B would silence session A's audio when A is still
                # the active one.
                t = (qs.get("transcript") or [""])[0]
                active = _get_active_transcript()
                if _synth is not None and (
                    not t or not active or Path(t) == active
                ):
                    _synth.extinguish()
                self._ok()
            elif path == "/ignite":
                # Tight initial buffer — the JSONL watcher takes over keeping
                # the bed alive once transcript appends start landing. Without
                # JSONL we'd silence 8s after the prompt; with JSONL, each
                # append extends by _alive_decay_seconds (≈2s).
                t = (qs.get("transcript") or [""])[0]
                if t:
                    _set_active_transcript(Path(t))
                if _synth is not None:
                    _synth.ignite(8)
                self._ok()
            elif path == "/reset":
                if _synth is not None:
                    _synth.reset_position()
                self._ok()
            elif path == "/samples":
                names = _synth.list_samples() if _synth is not None else []
                self._ok(json.dumps(names).encode(), "application/json")
            elif path == "/mode/random":
                if _synth is not None:
                    _synth.set_random_mode()
                self._ok(b"random")
            elif path == "/mode/fixed":
                name = (qs.get("sample") or [""])[0]
                if not name or _synth is None:
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b"sample query param required")
                    return
                if _synth.set_fixed_mode(name):
                    self._ok(f"fixed:{name}".encode())
                else:
                    self.send_response(404)
                    self.end_headers()
                    self.wfile.write(f"sample {name!r} not in pool".encode())
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *a, **kw):
            pass

    server = HTTPServer(("127.0.0.1", port), Handler)
    actual_port = server.server_port  # resolves the OS-picked port if port=0
    PORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    PORT_FILE.write_text(str(actual_port))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"[bridge] control http on http://127.0.0.1:{actual_port} "
          f"(port file: {PORT_FILE})")
    return server


def _is_alive_signal(raw_line: str) -> bool:
    """True for assistant/user transcript entries; false for system/etc.
    Same filter as before — keeps end-of-turn races (system stop_hook_summary
    entries that land after the Stop hook) from re-extending the bed."""
    try:
        evt = json.loads(raw_line)
    except json.JSONDecodeError:
        return False
    return isinstance(evt, dict) and evt.get("type") in ("assistant", "user")


async def session_watcher_loop():
    """Tail _active_transcript when set. Each new assistant/user line bumps
    the bed's alive window via touch_alive(), which uses the synth's
    _alive_decay_seconds (~2s) — so after the last JSONL append (turn
    finishes OR user presses Esc), the bed silences within that window.

    Reopens the tail handle whenever _active_transcript changes (i.e.,
    a different session's UserPromptSubmit hook fires)."""
    current_path: Optional[Path] = None
    f = None
    try:
        while True:
            target = _get_active_transcript()
            if target != current_path:
                if f:
                    f.close()
                    f = None
                if target is not None and target.exists():
                    f = target.open("r")
                    f.seek(0, 2)  # tail from end
                current_path = target
            line = f.readline() if f else None
            if line:
                if _synth is not None and _is_alive_signal(line):
                    _synth.touch_alive()
            else:
                if f:
                    f.seek(f.tell())
                await asyncio.sleep(0.2)
    finally:
        if f:
            f.close()


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bed-dir", required=True,
                    help="Directory of audio samples to use as the random-pick pool.")
    args = ap.parse_args()

    global _synth
    from synth import Synth

    # Preflight: refuse to start in environments with no audio output. This
    # catches headless containers, CI runners, and remote/cloud Claude Code
    # sandboxes — the plugin can only do anything useful on a host with
    # actual speakers/headphones. Exiting cleanly here lets the plugin
    # monitor surface "not supported on this host" instead of crashing
    # later inside sd.OutputStream() with a less obvious error.
    try:
        import sounddevice as sd  # noqa: F401  (Synth pulls it in too)
        outputs = [d for d in sd.query_devices() if d.get("max_output_channels", 0) > 0]
    except Exception as e:
        print(f"[let-him-cook] audio backend unavailable: {e}", file=sys.stderr)
        print("[let-him-cook] this plugin requires local audio (PortAudio + an "
              "output device). On Linux: apt install libportaudio2.", file=sys.stderr)
        sys.exit(1)
    if not outputs:
        print("[let-him-cook] no audio output device available — plugin not "
              "supported on this host (headless / remote sandbox).",
              file=sys.stderr)
        sys.exit(1)

    bed_dir = Path(args.bed_dir).expanduser()
    exts = {".mp3", ".wav", ".aiff", ".flac", ".ogg"}
    bed_samples = sorted(
        str(p) for p in bed_dir.iterdir()
        if p.is_file() and p.suffix.lower() in exts
    )
    if not bed_samples:
        raise RuntimeError(f"no audio files found in {bed_dir}")

    chimes_map: dict = {}
    intros_map: dict = {}
    chimes_path = bed_dir / "chimes.json"
    if chimes_path.exists():
        raw = json.loads(chimes_path.read_text())
        for bed_path in bed_samples:
            bed_name = Path(bed_path).name
            entry = raw.get(bed_name)
            if isinstance(entry, str):
                chimes_map[bed_path] = str(bed_dir / entry)
            elif isinstance(entry, dict):
                if entry.get("outro"):
                    chimes_map[bed_path] = str(bed_dir / entry["outro"])
                if entry.get("intro"):
                    intros_map[bed_path] = str(bed_dir / entry["intro"])

    _synth = Synth(profile="cooking",
                   bed_samples=bed_samples,
                   chimes=chimes_map,
                   intros=intros_map)
    _synth.start()
    print(f"[bridge] audio synth started — {len(bed_samples)} samples in {bed_dir}",
          flush=True)
    start_control_http()

    try:
        await session_watcher_loop()
    finally:
        _synth.stop()
        try:
            PORT_FILE.unlink()
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
