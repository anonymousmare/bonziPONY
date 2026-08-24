"""Tiny typewriter click player.

Used two ways: one click per revealed character in the speech bubble, and a short ``burst``
when a notification panel appears, so an alert sounds like it was typed out rather than
arriving in silence.

Synthesizes a short click WAV on first use (saved under assets/sounds/)
then plays it via QSoundEffect. Calls are throttled so rapid typing
doesn't stack effects. Safe no-op if QtMultimedia isn't available.
"""

from __future__ import annotations

import logging
import math
import random
import struct
import time
import wave
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

_SOUND_DIR = Path("assets/sounds")
_CLICK_PATH = _SOUND_DIR / "typewriter_click.wav"
_SAMPLE_RATE = 22050
_DURATION_S = 0.018          # ~18 ms click
_POOL_SIZE = 4               # overlapping players so rapid calls don't cut off
_MIN_INTERVAL_S = 0.025      # throttle: skip calls closer than this
_BURST_COUNT = 3             # clicks in a "something appeared" burst
_BURST_INTERVAL_MS = 45      # spacing within a burst; must clear _MIN_INTERVAL_S


def _synthesize_click_wav(path: Path) -> bool:
    """Write a short typewriter-style click to ``path``. Return True on success."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        n_samples = int(_SAMPLE_RATE * _DURATION_S)
        # Exponential-decay noise burst with a bit of mid-frequency body.
        # The fast decay envelope gives the "tick" character; the sine adds pitch.
        frames = bytearray()
        for i in range(n_samples):
            t = i / _SAMPLE_RATE
            env = math.exp(-t * 220.0)       # fast decay ~18ms
            noise = (random.random() * 2.0 - 1.0) * 0.6
            tone = math.sin(2.0 * math.pi * 1800.0 * t) * 0.35
            sample = (noise + tone) * env * 0.7
            sample = max(-1.0, min(1.0, sample))
            frames += struct.pack("<h", int(sample * 32767))
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(_SAMPLE_RATE)
            wf.writeframes(bytes(frames))
        return True
    except Exception as exc:
        logger.warning("Failed to synthesize typewriter click: %s: %s",
                       type(exc).__name__, exc)
        return False


class TypewriterSound:
    """Plays short click WAVs on demand. One instance per speech bubble."""

    def __init__(self) -> None:
        self._pool: List = []
        self._idx = 0
        self._last_play = 0.0
        self._ready = False
        self._enabled = True
        self._init_attempted = False

    def _init_if_needed(self) -> None:
        if self._init_attempted:
            return
        self._init_attempted = True
        if not _CLICK_PATH.exists():
            if not _synthesize_click_wav(_CLICK_PATH):
                self._enabled = False
                return
        try:
            from PyQt5.QtCore import QUrl
            from PyQt5.QtMultimedia import QSoundEffect
        except Exception as exc:
            logger.info("QtMultimedia unavailable — typewriter sound disabled: %s", exc)
            self._enabled = False
            return
        try:
            url = QUrl.fromLocalFile(str(_CLICK_PATH.resolve()))
            for _ in range(_POOL_SIZE):
                eff = QSoundEffect()
                eff.setSource(url)
                eff.setVolume(0.18)
                self._pool.append(eff)
            self._ready = True
        except Exception as exc:
            logger.warning("Typewriter QSoundEffect init failed: %s: %s",
                           type(exc).__name__, exc)
            self._enabled = False

    def set_enabled(self, on: bool) -> None:
        self._enabled = bool(on)

    def play(self) -> None:
        if not self._enabled:
            return
        self._init_if_needed()
        if not self._ready:
            return
        now = time.monotonic()
        if now - self._last_play < _MIN_INTERVAL_S:
            return
        self._last_play = now
        try:
            eff = self._pool[self._idx]
            self._idx = (self._idx + 1) % len(self._pool)
            eff.play()
        except Exception:
            pass

    def burst(self, count: int = _BURST_COUNT, interval_ms: int = _BURST_INTERVAL_MS) -> None:
        """A few clicks in quick succession — something arriving, rather than being typed.

        One click is a tick nobody notices; three at typing speed reads as the machine
        starting a line. Spacing stays above the throttle in ``play`` so every click lands.
        Scheduled with ``QTimer.singleShot``, so this must be called from the GUI thread —
        every caller is a widget reacting to a queued signal, which already is one.
        """
        if not self._enabled:
            return
        self._init_if_needed()
        if not self._ready:
            return
        self.play()
        gap = max(interval_ms, int(_MIN_INTERVAL_S * 1000) + 5)
        try:
            from PyQt5.QtCore import QTimer

            for index in range(1, max(1, count)):
                QTimer.singleShot(index * gap, self.play)
        except Exception:
            pass  # one click is still better than none
