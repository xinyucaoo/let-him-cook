#!/usr/bin/env python3
"""bridge.py — let-him-cook audio bridge.

Holds a single output audio stream and tails the active Claude Code session
JSONL to keep the bed alive while the assistant is working. Hooks ping the
HTTP control endpoint:

    GET /ignite                       — start the bed
    GET /extinguish                   — stop the bed (Stop hook)
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
_synth = None  # type: ignore[var-annotated]


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
                if _synth is not None:
                    _synth.extinguish()
                self._ok()
            elif path == "/ignite":
                # Wide window — covers any normal turn length without needing
                # a JSONL-based heartbeat. The Stop hook is the authoritative
                # silence signal; if it doesn't fire (Esc interrupt), the bed
                # plays out the full 600s tail before going silent. Cost of
                # not having an interrupt hook upstream.
                if _synth is not None:
                    _synth.ignite(600)
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

    # No JSONL tailing anymore. Previously the bridge tailed whichever Claude
    # Code session's transcript was most recently modified across the whole
    # machine, then called touch_alive() on every assistant/user line. With
    # multiple parallel sessions that meant any background Claude appending
    # to its JSONL kept the bed alive — audible cross-session bleed. The
    # bridge is now purely hook-driven: /ignite buys 600s, /extinguish kills
    # instantly. We just sit here keeping the HTTP server and audio thread
    # alive forever.
    try:
        while True:
            await asyncio.sleep(3600)
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
