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

PROJECTS_DIR = Path(os.path.expanduser("~/.claude/projects"))
_synth = None  # type: ignore[var-annotated]


def start_control_http(port: int = 8766):
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
                # Tight bootstrap window — JSONL appends via touch_alive() carry
                # the rest of the turn. Manual interrupts (which don't fire the
                # Stop hook) silence the bed within ~3s of the last JSONL event.
                if _synth is not None:
                    _synth.ignite(6)
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
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"[bridge] control http on http://127.0.0.1:{port}")
    return server


def find_latest_jsonl() -> Optional[Path]:
    candidates = list(PROJECTS_DIR.glob("*/*.jsonl"))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _is_alive_signal(raw_line: str) -> bool:
    """True if this transcript entry means claude is actively working.
    Excludes system/* entries (informational, stop_hook_summary) that can
    land AFTER the Stop hook has run, which would otherwise reignite
    the bed over the end-of-turn boundary.
    """
    try:
        evt = json.loads(raw_line)
    except json.JSONDecodeError:
        return False
    return isinstance(evt, dict) and evt.get("type") in ("assistant", "user")


async def session_watcher():
    """Tail the active Claude Code session jsonl. Each new assistant/user line
    extends the bed's alive window via touch_alive()."""
    current: Optional[Path] = None
    f = None
    try:
        while True:
            target = find_latest_jsonl()
            if target and target != current:
                if f:
                    f.close()
                f = target.open("r")
                f.seek(0, 2)  # tail from end
                current = target
                print(f"[watch] tracking {target}", flush=True)
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
        await session_watcher()
    finally:
        _synth.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
