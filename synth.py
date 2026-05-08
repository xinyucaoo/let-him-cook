"""Audio synth — sample-bed playback plus a procedural crackle synth.

Mixes a looping ambience bed (loaded from disk or procedurally generated) with
transient crackle/pop voices. Driven by bridge.py, which ignites the bed at
turn start and extinguishes it when the Stop hook fires.
"""

import math
import threading
import time
from pathlib import Path
from typing import List, Optional

import numpy as np
import sounddevice as sd


SAMPLE_RATE = 44100


def _biquad_bp(x: np.ndarray, center_hz: float, Q: float, sr: int = SAMPLE_RATE) -> np.ndarray:
    """RBJ bandpass biquad, evaluated sample-by-sample. Short signals only."""
    w0 = 2.0 * math.pi * center_hz / sr
    cosw = math.cos(w0)
    sinw = math.sin(w0)
    alpha = sinw / (2.0 * Q)
    a0 = 1.0 + alpha
    b0 = alpha / a0
    b2 = -alpha / a0
    a1 = (-2.0 * cosw) / a0
    a2 = (1.0 - alpha) / a0

    y = np.zeros_like(x)
    x1 = x2 = y1 = y2 = 0.0
    for i in range(x.shape[0]):
        xi = float(x[i])
        yi = b0 * xi + b2 * x2 - a1 * y1 - a2 * y2
        x2 = x1; x1 = xi
        y2 = y1; y1 = yi
        y[i] = yi
    return y


def _load_sample_loop(path: str) -> np.ndarray:
    """Load any audio file into a mono float32 array at SAMPLE_RATE.

    Mixes stereo to mono. Linearly resamples if the sample rate differs.
    Normalizes to peak 0.6 so it sits at a sane bed level regardless of
    the source recording's volume.
    """
    import soundfile as sf  # lazy: avoid hard dep when bed_sample unused
    data, sr = sf.read(path, dtype="float32", always_2d=False)
    if data.ndim == 2:
        data = data.mean(axis=1)
    if sr != SAMPLE_RATE:
        # naive linear resample — fine for ambience beds
        n_out = int(round(data.shape[0] * SAMPLE_RATE / sr))
        x = np.linspace(0.0, data.shape[0] - 1, n_out, dtype=np.float32)
        i0 = np.floor(x).astype(np.int64)
        i1 = np.minimum(i0 + 1, data.shape[0] - 1)
        frac = (x - i0).astype(np.float32)
        data = (data[i0] * (1.0 - frac) + data[i1] * frac).astype(np.float32)
    peak = float(np.abs(data).max()) or 1.0
    data = data * (0.95 / peak)
    return data.astype(np.float32)


def _make_brown_bed(seconds: float = 2.0) -> np.ndarray:
    """2s brown-noise loop, lowpassed at ~600 Hz."""
    n = int(SAMPLE_RATE * seconds)
    rng = np.random.default_rng()
    white = rng.uniform(-1.0, 1.0, n).astype(np.float32)
    bed = np.empty(n, dtype=np.float32)
    last = 0.0
    for i in range(n):
        last = (last + 0.02 * float(white[i])) / 1.02
        bed[i] = last * 3.5

    # 1-pole IIR lowpass at 600 Hz
    rc = 1.0 / (2.0 * math.pi * 600.0)
    dt = 1.0 / SAMPLE_RATE
    alpha = dt / (rc + dt)
    out = np.empty(n, dtype=np.float32)
    prev = 0.0
    for i in range(n):
        prev = prev + alpha * (float(bed[i]) - prev)
        out[i] = prev
    return out


def _resonant_ping(rng: np.random.Generator) -> np.ndarray:
    """One micro-pop modeled as a high-Q bandpass kicked by a noise impulse.

    Real oil pops are tiny steam bubbles bursting in liquid — they ring with a
    pitched 'tic' and trail off into a short noise splatter, not flat hiss.
    """
    pop_len = int(rng.uniform(0.005, 0.018) * SAMPLE_RATE)  # 5–18ms
    # short noise impulse to excite the resonator
    excite = rng.uniform(-1.0, 1.0, pop_len).astype(np.float32)
    # taper the excitation hard — most energy in the first 2ms
    head = max(2, int(0.0015 * SAMPLE_RATE))
    excite[:head] *= np.linspace(1.0, 1.0, head, dtype=np.float32)
    decay = np.exp(-np.arange(pop_len, dtype=np.float32) /
                   max(1, int(pop_len * 0.18)))
    excite *= decay

    # high-Q bandpass at a random pitch — distribution of pop pitches is what
    # makes the bed feel like many simultaneous bubbles
    center = float(rng.uniform(1400.0, 4200.0))
    Q = float(rng.uniform(8.0, 22.0))
    rung = _biquad_bp(excite, center, Q)
    # slight noise splatter after the ping
    splat_len = int(rng.uniform(0.002, 0.006) * SAMPLE_RATE)
    if splat_len > 0:
        sp = rng.uniform(-1.0, 1.0, splat_len).astype(np.float32)
        sp *= np.exp(-np.arange(splat_len, dtype=np.float32) /
                     max(1, splat_len * 0.4)) * 0.35
        out = np.zeros(pop_len + splat_len, dtype=np.float32)
        out[:pop_len] += rung
        out[pop_len:] += sp
        return out
    return rung


def _make_sizzle_bed(seconds: float = 4.0) -> np.ndarray:
    """Oil-sizzle loop built from clustered resonant pops + thin hiss.

    Density modulates over time so the bed sounds like real frying — the pan
    has 'busier' moments and 'calmer' moments, not a uniform texture.
    """
    n = int(SAMPLE_RATE * seconds)
    rng = np.random.default_rng()
    out = np.zeros(n, dtype=np.float32)

    # Build a slowly-varying density envelope (sum of a few low-freq sinusoids
    # at incommensurate rates so it doesn't repeat audibly within the loop).
    t = np.arange(n, dtype=np.float32) / SAMPLE_RATE
    density = (
        0.55
        + 0.25 * np.sin(2.0 * np.pi * 0.7 * t + 1.2)
        + 0.18 * np.sin(2.0 * np.pi * 1.6 * t + 3.1)
        + 0.10 * np.sin(2.0 * np.pi * 2.3 * t + 0.4)
    ).clip(0.1, 1.0)

    # Walk through the bed at small steps, drawing pops with rate ∝ density
    # (Poisson-ish thinning). Average pop rate ~450/s when density=1.
    base_rate = 450.0
    step = 64  # samples between density samples (~1.5ms)
    i = 0
    while i < n - 600:
        local_rate = base_rate * float(density[i])
        # expected pops in this step window
        expected = local_rate * step / SAMPLE_RATE
        n_pops_here = int(rng.poisson(expected))
        for _ in range(n_pops_here):
            pop = _resonant_ping(rng) * float(rng.uniform(0.35, 1.0))
            offset = int(rng.integers(0, step))
            start = i + offset
            end = start + pop.shape[0]
            if end <= n:
                out[start:end] += pop
        i += step

    # thin background hiss — much quieter than the pops, just to fill gaps
    white = rng.uniform(-1.0, 1.0, n).astype(np.float32)
    rc = 1.0 / (2.0 * math.pi * 2200.0)
    dt = 1.0 / SAMPLE_RATE
    alpha = dt / (rc + dt)
    hiss = np.empty(n, dtype=np.float32)
    lp_prev = 0.0
    for j in range(n):
        lp_prev = lp_prev + alpha * (float(white[j]) - lp_prev)
        hiss[j] = float(white[j]) - lp_prev

    bed = out + hiss * 0.08
    peak = float(np.abs(bed).max()) or 1.0
    bed *= 0.55 / peak
    return bed.astype(np.float32)


class Synth:
    """Real-time mixer: brown-noise bed + transient crackle voices."""

    def __init__(self, master_gain: float = 1.0, crackle_gain: float = 0.7,
                 bed_gain: float = 0.6, blocksize: int = 512,
                 profile: str = "fire", bed_sample: Optional[str] = None,
                 bed_samples: Optional[List[str]] = None,
                 chimes: Optional[dict] = None,
                 intros: Optional[dict] = None):
        self.master_gain = master_gain
        self.crackle_gain = crackle_gain
        self.bed_user_gain = bed_gain
        self.blocksize = blocksize
        self.profile = profile
        # bed_sample: single fixed sample. bed_samples: pool to randomly pick
        # from on each ignite. bed_sample acts as the "any sample loaded" flag
        # for sample-bed mode (gain stack, crackle suppression).
        self.bed_sample = bed_sample or (bed_samples[0] if bed_samples else None)

        self._lock = threading.Lock()
        self._voices: List[List] = []      # each: [pos, samples]
        self._token_times: List[float] = []  # rolling window for tps
        self._bg_phase = 0
        self._bg_target_gain = 0.0
        self._bg_current_gain = 0.0
        self._rng = np.random.default_rng()

        # "Bonfire" mode: while a claude turn is actively in progress, the bed
        # holds a floor gain and we self-trigger random crackles so the fire
        # sounds continuous instead of going silent between content blocks.
        self._alive_until: float = 0.0       # epoch deadline; before now() = idle
        # Long backstop: end-of-turn is now signalled semantically by the
        # bridge (assistant entry with terminal stop_reason → extinguish),
        # so this decay window only catches pathological cases where no
        # terminal entry ever lands — typically an Esc interrupt, a process
        # crash, or network failure. 60s is long enough that legitimate
        # tool execution gaps don't trip it (most tools complete in <60s)
        # and short enough that an interrupted turn doesn't hang forever.
        self._alive_decay_seconds: float = 60.0
        self._next_auto_crackle: float = 0.0  # next epoch-time auto-fire
        self._sample_clock: int = 0           # frames produced (for callback timing)
        self._muted: bool = True              # extinguish→True, ignite→False;
                                              # blocks touch_alive races from JSONL
                                              # tail catching up after Stop hook fires.

        # Mode: "random" picks a fresh bed on each ignite (default when a
        # pool is loaded). "fixed" locks to a single bed across turns and
        # preserves loop position so a long ambience flows continuously.
        self._mode: str = "random"
        self._fixed_idx: int = 0

        # Pre-load the random-pick pool (if any) so ignite is snappy. The
        # active buffer rotates among these on each ignite.
        # Parallel lists track per-pool one-shots:
        #   _bg_pool_intros: played at ignite (e.g. microwave start beep)
        #   _bg_pool_chimes: played at extinguish (e.g. microwave end beep)
        # None for beds with no associated one-shot at that boundary.
        self._bg_pool: List[np.ndarray] = []
        self._bg_pool_paths: List[str] = []
        self._bg_pool_intros: List[Optional[np.ndarray]] = []
        self._bg_pool_chimes: List[Optional[np.ndarray]] = []
        self._active_chime: Optional[np.ndarray] = None
        if bed_samples:
            for p in bed_samples:
                self._bg_pool.append(_load_sample_loop(p))
                self._bg_pool_paths.append(p)
                intro_path = (intros or {}).get(p)
                chime_path = (chimes or {}).get(p)
                self._bg_pool_intros.append(
                    _load_sample_loop(intro_path) if intro_path else None
                )
                self._bg_pool_chimes.append(
                    _load_sample_loop(chime_path) if chime_path else None
                )
            self._bg_buffer = self._bg_pool[0]
        elif bed_sample:
            self._bg_buffer = _load_sample_loop(bed_sample)
        elif self.profile == "cooking":
            self._bg_buffer = _make_sizzle_bed(4.0)
        else:
            self._bg_buffer = _make_brown_bed(2.0)
        self._bg_len = self._bg_buffer.shape[0]

        self._stream: Optional[sd.OutputStream] = None

    def start(self):
        self._stream = sd.OutputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            blocksize=self.blocksize,
            callback=self._callback,
        )
        self._stream.start()

    def stop(self):
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def extinguish(self):
        """Snap the bed out — bg snaps to silence and any active chime plays
        as a one-shot (e.g. microwave ding after the running sound).

        Called by the Stop hook. Also sets a muted flag that blocks subsequent
        touch_alive() calls — the JSONL watcher tail can land assistant
        entries AFTER this fires (file IO races with the hook), and without
        the mute they'd reignite the bed for another 4s.
        """
        with self._lock:
            self._muted = True
            self._alive_until = 0.0
            self._bg_target_gain = 0.0
            self._bg_current_gain = 0.0
            self._voices = []
            if self._active_chime is not None:
                self._voices.append([0, self._active_chime])
                self._active_chime = None

    def list_samples(self) -> List[str]:
        """Return basenames of pool samples, in order, for /samples HTTP."""
        return [Path(p).name for p in self._bg_pool_paths]

    def reset_position(self):
        """Rewind the bed loop to frame 0 AND silence the bed so the position
        doesn't drift before the next ignite. Without the silence, the audio
        callback keeps advancing _bg_phase while a turn is still alive — so
        a /reset called mid-response would zero the phase, then immediately
        advance away from 0, defeating the 'start over' intent."""
        with self._lock:
            self._bg_phase = 0
            self._muted = True
            self._alive_until = 0.0
            self._bg_target_gain = 0.0
            self._bg_current_gain = 0.0
            self._voices = []

    def set_random_mode(self):
        with self._lock:
            self._mode = "random"

    def set_fixed_mode(self, sample_name: str) -> bool:
        """Lock the bed to one sample by basename. Returns False if the name
        isn't in the pool — caller surfaces a 404."""
        with self._lock:
            for i, path in enumerate(self._bg_pool_paths):
                if Path(path).name == sample_name:
                    self._mode = "fixed"
                    self._fixed_idx = i
                    # Take effect on the next ignite. If we're currently
                    # alive, swap the buffer in place so the user hears the
                    # change without waiting for the next turn.
                    if not self._muted:
                        self._bg_buffer = self._bg_pool[i]
                        self._bg_len = self._bg_buffer.shape[0]
                        self._bg_phase = 0
                        self._active_chime = self._bg_pool_chimes[i]
                    return True
            return False

    def ignite(self, seconds: float = 300.0):
        """Light the bonfire and clear the mute. Called by the /ignite hook on
        UserPromptSubmit so the next turn can play.

        If a sample pool was loaded, randomly pick one as the active bed and
        reset _bg_phase to 0 (since each pick is a different sample, resume-
        from-position doesn't apply). For a single fixed sample, position is
        preserved across turns."""
        with self._lock:
            self._muted = False
            self._alive_until = max(self._alive_until, time.time() + seconds)
            if self._bg_pool:
                if self._mode == "fixed":
                    idx = self._fixed_idx
                    # Don't reset _bg_phase — fixed mode preserves loop
                    # position across turns for continuous ambience.
                else:
                    idx = int(self._rng.integers(0, len(self._bg_pool)))
                    self._bg_phase = 0
                self._bg_buffer = self._bg_pool[idx]
                self._bg_len = self._bg_buffer.shape[0]
                self._active_chime = self._bg_pool_chimes[idx]
                intro = self._bg_pool_intros[idx]
                if intro is not None:
                    # Queue intro as a one-shot voice (e.g. microwave start
                    # beep) so it plays once over the bed at turn start.
                    self._voices.append([0, intro])

    def touch_alive(self, seconds: Optional[float] = None):
        """Mark the session as actively producing. Extends the bonfire window.

        No-op while muted, so late JSONL appends after Stop hook can't
        reignite the bed.
        """
        d = seconds if seconds is not None else self._alive_decay_seconds
        with self._lock:
            if self._muted:
                return
            self._alive_until = max(self._alive_until, time.time() + d)

    def trigger(self, text: str):
        """One token-text event from the bridge → one crackle (+ maybe sub-pop).

        When a bed_sample is loaded, the sample IS the soundscape — synthesized
        crackles on top fight the recorded ambience, so we skip voice creation
        and just keep tps tracking alive for bed gain modulation.
        """
        if not text:
            return
        n_tokens = max(1, (len(text) + 2) // 3)
        now = time.time()
        with self._lock:
            for _ in range(n_tokens):
                self._token_times.append(now)
            self._cull_old_tokens(now)
            if self.bed_sample:
                return
            tps = len(self._token_times) / 2.0
            intensity = min(1.0, tps / 25.0)
            self._bg_target_gain = min(0.45, tps / 30.0) * self.bed_user_gain

            crackle = self._build_crackle(intensity)
            self._voices.append([0, crackle])
            if self._rng.random() < 0.25 + intensity * 0.4:
                self._voices.append([0, self._build_sub_pop(intensity)])

    def _cull_old_tokens(self, now: float):
        cutoff = now - 2.0
        i = 0
        while i < len(self._token_times) and self._token_times[i] < cutoff:
            i += 1
        if i:
            del self._token_times[:i]

    def _build_crackle(self, intensity: float) -> np.ndarray:
        if self.profile == "cooking":
            # Shorter, brighter "oil pop": 15–40ms, 2.5–6 kHz, sharper Q
            dur = 0.015 + float(self._rng.random()) * 0.025
        else:
            dur = 0.04 + float(self._rng.random()) * 0.08
        n = int(SAMPLE_RATE * dur)
        # noise burst with linear front-to-back taper
        noise = (self._rng.uniform(-1.0, 1.0, n) *
                 (1.0 - np.arange(n, dtype=np.float32) / n)).astype(np.float32)
        if self.profile == "cooking":
            center = 2500.0 + intensity * 2500.0 + float(self._rng.random()) * 1000.0
            Q = 2.0 + float(self._rng.random()) * 3.0
        else:
            center = 800.0 + intensity * 2800.0 + float(self._rng.random()) * 1200.0
            Q = 1.2 + float(self._rng.random()) * 2.0
        filtered = _biquad_bp(noise, center, Q)
        # envelope: 3ms linear attack, exp decay to 0.001 over remaining
        peak = (0.15 + intensity * 0.55) * (0.6 + float(self._rng.random()) * 0.6)
        env = np.empty(n, dtype=np.float32)
        attack_n = max(1, int(0.003 * SAMPLE_RATE))
        attack_n = min(attack_n, n)
        env[:attack_n] = np.linspace(0.0, peak, attack_n, dtype=np.float32)
        rem = n - attack_n
        if rem > 0:
            decay = peak * np.exp(np.log(0.001 / max(peak, 1e-6)) *
                                  np.arange(rem, dtype=np.float32) / rem)
            env[attack_n:] = decay
        return (filtered * env * self.crackle_gain).astype(np.float32)

    def _build_sub_pop(self, intensity: float) -> np.ndarray:
        if self.profile == "cooking":
            # Bubble blip: sweep UP from low to mid, like a bubble surfacing
            sweep_dur = 0.06
            total = 0.10
            n = int(SAMPLE_RATE * total)
            t = np.arange(n, dtype=np.float32) / SAMPLE_RATE
            f0 = 90.0 + float(self._rng.random()) * 60.0
            f1 = 280.0 + float(self._rng.random()) * 140.0
            ratio = f1 / f0
            ts = np.minimum(t, sweep_dur)
            ph_sweep = f0 * sweep_dur / math.log(ratio) * (np.power(ratio, ts / sweep_dur) - 1.0)
            ph_after = np.maximum(0.0, t - sweep_dur) * f1
            ph = ph_sweep + ph_after
            sig = np.sin(2.0 * np.pi * ph).astype(np.float32)
            peak = 0.10 + intensity * 0.12
            env = np.zeros(n, dtype=np.float32)
            attack_n = max(1, int(0.008 * SAMPLE_RATE))
            env[:attack_n] = np.linspace(0.0001, peak, attack_n, dtype=np.float32)
            decay_n = int(0.07 * SAMPLE_RATE)
            decay_end = min(n, attack_n + decay_n)
            rem = decay_end - attack_n
            if rem > 0:
                env[attack_n:decay_end] = peak * np.exp(
                    np.log(0.0001 / max(peak, 1e-6)) *
                    np.arange(rem, dtype=np.float32) / rem
                )
            return (sig * env * self.crackle_gain).astype(np.float32)
        sweep_dur = 0.05
        total = 0.08
        n = int(SAMPLE_RATE * total)
        t = np.arange(n, dtype=np.float32) / SAMPLE_RATE
        f0 = 180.0 + float(self._rng.random()) * 220.0
        f1 = 60.0
        ratio = f1 / f0
        # phase from instantaneous freq f(t) = f0 * ratio^(t/sweep_dur) for t<=sweep_dur,
        # then constant f1. Integrate analytically.
        ts = np.minimum(t, sweep_dur)
        ph_sweep = f0 * sweep_dur / math.log(ratio) * (np.power(ratio, ts / sweep_dur) - 1.0)
        ph_after = np.maximum(0.0, t - sweep_dur) * f1
        ph = ph_sweep + ph_after
        sig = np.sin(2.0 * np.pi * ph).astype(np.float32)

        peak = 0.08 + intensity * 0.15
        env = np.zeros(n, dtype=np.float32)
        attack_n = max(1, int(0.005 * SAMPLE_RATE))
        env[:attack_n] = np.linspace(0.0001, peak, attack_n, dtype=np.float32)
        decay_n = int(0.06 * SAMPLE_RATE)
        decay_end = min(n, attack_n + decay_n)
        rem = decay_end - attack_n
        if rem > 0:
            env[attack_n:decay_end] = peak * np.exp(
                np.log(0.0001 / max(peak, 1e-6)) *
                np.arange(rem, dtype=np.float32) / rem
            )
        return (sig * env * self.crackle_gain).astype(np.float32)

    def _callback(self, outdata, frames, time_info, status):
        if status:
            # underflows etc — non-fatal, just keep going
            pass
        out = np.zeros(frames, dtype=np.float32)
        with self._lock:
            # background bed (looped). Only advance _bg_phase while the bed
            # is actually audible — otherwise a /reset called between turns
            # would zero the phase and the callback would immediately drift
            # it back into the middle of the song before the next ignite.
            audible = (self._bg_target_gain > 0.0 or self._bg_current_gain > 0.0)
            if audible:
                end = self._bg_phase + frames
                if end <= self._bg_len:
                    bg = self._bg_buffer[self._bg_phase:end]
                else:
                    a = self._bg_buffer[self._bg_phase:]
                    b = self._bg_buffer[: end - self._bg_len]
                    bg = np.concatenate([a, b])
                self._bg_phase = end % self._bg_len
            else:
                bg = np.zeros(frames, dtype=np.float32)

            # ramp bg gain toward target. Slow ease (150ms) when ramping up so
            # the bed swells in smoothly; fast (15ms) when ramping down so
            # extinguish snaps cleanly without an audible tail.
            tc = 0.015 if self._bg_target_gain < self._bg_current_gain else 0.15
            ramp_alpha = min(1.0, frames / SAMPLE_RATE / tc)
            new_gain = (self._bg_current_gain +
                        (self._bg_target_gain - self._bg_current_gain) * ramp_alpha)
            ramp = np.linspace(self._bg_current_gain, new_gain, frames, dtype=np.float32)
            self._bg_current_gain = new_gain
            out += bg * ramp

            # transient voices
            keep = []
            for v in self._voices:
                pos, samples = v
                avail = samples.shape[0] - pos
                if avail <= frames:
                    out[:avail] += samples[pos:]
                    # voice ends
                else:
                    out += samples[pos:pos + frames]
                    v[0] = pos + frames
                    keep.append(v)
            self._voices = keep

            # decay tps tracking + bg target if no new tokens
            now = time.time()
            self._cull_old_tokens(now)
            alive = now < self._alive_until
            if alive:
                if self.bed_sample:
                    # Sample-bed mode: the recording IS the soundscape, so
                    # target near unity. The original tps-modulated gain was
                    # designed to leave headroom for crackle voices on top —
                    # with no crackles, that headroom is just lost loudness.
                    self._bg_target_gain = 1.0
                else:
                    tps = len(self._token_times) / 2.0
                    base_target = max(min(0.45, tps / 30.0), 0.30)
                    self._bg_target_gain = base_target * self.bed_user_gain
            else:
                # Not alive → bed must be silent. Don't let stale tps from a
                # finished turn ramp the bed back up after extinguish.
                self._bg_target_gain = 0.0

            # Auto-crackles while alive: fire a small random crackle on a
            # randomized 80–220ms cadence so the fire sounds continuous rather
            # than silent between real content blocks. Skipped when a bed
            # sample is loaded — the recorded loop carries the texture.
            if alive and not self.bed_sample and now >= self._next_auto_crackle:
                amb_intensity = 0.15 + float(self._rng.random()) * 0.25
                self._voices.append([0, self._build_crackle(amb_intensity)])
                if self._rng.random() < 0.18:
                    self._voices.append([0, self._build_sub_pop(amb_intensity)])
                self._next_auto_crackle = now + 0.08 + float(self._rng.random()) * 0.14

        np.multiply(out, self.master_gain, out=out)
        np.clip(out, -1.0, 1.0, out=out)
        outdata[:, 0] = out
