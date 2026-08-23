"""
PonyManager — the one-element holder for the active character.

This was the coordinator for a multi-pony desktop: adding and removing ponies,
routing speech between them, and running inter-pony conversations. The advisor
build is a single character, so all of that is gone. What is left is the seam
main.py, pipeline.py and pet_window.py already reach through — ``primary``,
``ponies``, and the collision-avoidance query — kept so those call sites do not
have to care whether a manager exists at all.

``route_user_speech`` and ``get_pony_by_slug`` still work and still do the right
thing for one pony. They are the entry points a second character would need
first, so removing them would cost more than it saves.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from core.pony_instance import PonyInstance
    from core.tts_queue import TTSQueue

logger = logging.getLogger(__name__)


class PonyManager:
    """Holds the active character and the resources shared with it."""

    def __init__(
        self,
        config: Any,
        ponies_root: Path,
        tts_queue: "TTSQueue",
    ) -> None:
        self.config = config
        self.ponies_root = ponies_root
        self.tts_queue = tts_queue

        self.ponies: list["PonyInstance"] = []
        self._menu_builder_factory: Any = None  # set by main.py
        self._screen_monitor: Any = None  # set by main.py for HWND exclusion
        self._shutting_down: bool = False

    @property
    def primary(self) -> Optional["PonyInstance"]:
        """The active character."""
        return self.ponies[0] if self.ponies else None

    def get_other_pony_positions(self, exclude) -> list[tuple[int, int]]:
        """Center positions of every pony except *exclude* (a PetWindow).

        Used for collision avoidance in movement ticks. With one character this
        is always empty, which is exactly what the caller wants to hear.
        """
        positions = []
        for p in self.ponies:
            pw = getattr(p, "pet_window", None)
            if pw is None or pw is exclude:
                continue
            try:
                positions.append((pw.x() + pw.width() // 2,
                                  pw.y() + pw.height() // 2))
            except Exception:
                pass
        return positions

    # ── Lifecycle ──────────────────────────────────────────────────

    def register_primary(self, instance: "PonyInstance") -> None:
        """Register the character (already constructed by main.py)."""
        instance.is_primary = True
        if self.ponies:
            self.ponies.insert(0, instance)
        else:
            self.ponies.append(instance)
        logger.info("Character registered: %s", instance.display_name)

    # ── Speech routing ─────────────────────────────────────────────

    def route_user_speech(self, text: str) -> Optional["PonyInstance"]:
        """Decide which character should answer. There is only one."""
        del text
        return self.primary

    def get_pony_by_slug(self, slug: str) -> Optional["PonyInstance"]:
        """Find a character by slug. Returns the first match."""
        for pony in self.ponies:
            if pony.slug == slug:
                return pony
        return None
