"""
PonyInstance — bundles the per-character state main.py wires together.

The character on the desktop gets one PonyInstance containing its own:
- GUI widgets (PetWindow, SpeechBubble, HeardText)
- Sprite/behavior managers
- LLM provider (own history, shared API config)
- PromptConfig (own system prompt)
- TTS voice slug

This was the per-pony bundle of a multi-pony desktop. The advisor build has one
character, so the secondary-pony factory and the companion bookkeeping are gone;
what remains is the bundle itself, which main.py still constructs directly.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from llm.base import LLMProvider
    from llm.prompt import PromptConfig

logger = logging.getLogger(__name__)


# Keyword aliases for speech routing (name detection)
_NAME_KEYWORDS: dict[str, list[str]] = {
    "rainbow_dash":              ["rainbow dash", "dash", "dashie", "rd"],
    "twilight_sparkle":          ["twilight sparkle", "twilight", "twi", "twily"],
    "princess_twilight_sparkle": ["twilight sparkle", "twilight", "twi", "twily"],
    "pinkie_pie":                ["pinkie pie", "pinkie", "pinks"],
    "rarity":                    ["rarity", "rares", "rari"],
    "fluttershy":                ["fluttershy", "flutter", "shy", "flutters"],
    "applejack":                 ["applejack", "aj", "apple jack"],
    "spike":                     ["spike", "spikey", "spikeywikey"],
    "trixie":                    ["trixie", "trix"],
    "starlight_glimmer":         ["starlight", "glimmer", "starlight glimmer"],
    "princess_celestia":         ["celestia", "princess celestia", "tia"],
    "princess_luna":             ["luna", "princess luna", "lulu", "woona"],
    "princess_cadance":          ["cadance", "cadence", "princess cadance"],
    "discord":                   ["discord"],
}


def _get_keywords_for(slug: str) -> list[str]:
    """Return name keywords for a character slug, longest first."""
    if slug in _NAME_KEYWORDS:
        kws = list(_NAME_KEYWORDS[slug])
    else:
        # Auto-generate from slug: "apple_bloom" → ["apple bloom"]
        display = slug.replace("_", " ")
        kws = [display]
        # Also add first word if multi-word
        parts = display.split()
        if len(parts) > 1:
            kws.append(parts[0])
    # Sort longest first so "rainbow dash" matches before "dash"
    kws.sort(key=len, reverse=True)
    return kws


class PonyInstance:
    """All state for one pony on the desktop."""

    def __init__(
        self,
        slug: str,
        display_name: str,
        is_primary: bool,
        prompt_config: "PromptConfig",
        llm: "LLMProvider",
        pet_window: Any,
        pet_controller: Any,
        speech_bubble: Any,
        heard_text: Any,
        sprite_manager: Any,
        behavior_manager: Any,
        effect_renderer: Any,
        pony_dir: Path,
        name_keywords: list[str] | None = None,
    ) -> None:
        self.slug = slug
        self.display_name = display_name
        self.is_primary = is_primary
        self.prompt_config = prompt_config
        self.llm = llm
        self.pet_window = pet_window
        self.pet_controller = pet_controller
        self.speech_bubble = speech_bubble
        self.heard_text = heard_text
        self.sprite_manager = sprite_manager
        self.behavior_manager = behavior_manager
        self.effect_renderer = effect_renderer
        self.pony_dir = pony_dir
        self.name_keywords = name_keywords or _get_keywords_for(slug)
        self.agent_loop: Any = None  # set externally for primary
        self._destroyed: bool = False

        # Check if this character has a TTS voice
        try:
            from tts.openai_compatible_tts import has_pvt_voice
            self.has_voice = has_pvt_voice(slug)
        except Exception:
            self.has_voice = True  # assume yes if we can't check

    def get_window_center(self) -> tuple[int, int]:
        """Return (cx, cy) of this pony's PetWindow."""
        w = self.pet_window
        return (w.x() + w.width() // 2, w.y() + w.height() // 2)

