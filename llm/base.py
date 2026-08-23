"""Abstract LLM provider interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable, Optional


class LLMProvider(ABC):
    """Base class for all LLM backends.

    Subclasses may set ``system_prompt_fn`` to override the default
    ``get_system_prompt()`` call used in ``chat()``.  Multi-pony mode
    uses this so each pony gets its own prompt from *PromptConfig*.
    """

    system_prompt_fn: Optional[Callable[[], str]] = None
    character_name: Optional[str] = None  # per-pony override for prefill

    @abstractmethod
    def chat(self, user_message: str) -> str:
        """Send a user message and return the assistant's response."""

    @abstractmethod
    def reset_history(self) -> None:
        """Clear conversation history."""

    @abstractmethod
    def generate_once(self, prompt: str, max_tokens: int | None = None,
                      system_prompt: str | None = None) -> str:
        """One-shot generation that does NOT affect conversation history.

        If system_prompt is provided, it overrides the default character
        system prompt.  Use this for utility tasks (summarization, profile
        extraction) that should NOT be in-character.
        """

    def call_with_tools(
        self,
        prompt: str,
        tools: "list[dict]",
        dispatch: "Callable[[str, dict], str]",
        max_rounds: int = 5,
        system_prompt: Optional[str] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """Answer a prompt, calling tools as needed, and return the final text.

        A separate method rather than a flag on ``chat``. ``chat`` returns ``str``, and a tool
        loop has to return either something structured or the text after several round trips;
        widening that signature would touch every provider and every call site for the sake of
        one caller. This runs the whole loop internally and hands back only the words.

        Like ``generate_once``, it does not touch conversation history: the tool traffic is
        working-out, not something the character should later remember saying.

        ``dispatch(name, arguments) -> str`` runs one tool and returns its result as text.
        Raising from it is fine; the error is handed back to the model, which usually recovers
        by trying something else.

        Only implemented where the backend actually supports tool use.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not support tool calling"
        )

    def supports_tools(self) -> bool:
        """Whether ``call_with_tools`` will work on this provider."""
        return type(self).call_with_tools is not LLMProvider.call_with_tools

    def has_history(self) -> bool:
        """Return True if there is any conversation history to summarize."""
        return False

    def describe_image(self, jpeg_bytes: bytes) -> Optional[str]:
        """
        One-shot vision call: describe what's in the image.
        Returns a plain-text description, or None if unsupported.
        Override in providers that support vision.
        """
        return None

    def describe_screen(self, jpeg_bytes: bytes) -> Optional[str]:
        """
        One-shot vision call: describe what's on a computer screen.
        Returns a plain-text description, or None if unsupported.
        Override in providers that support vision.
        """
        return None

    def inject_history(self, user_message: str, assistant_message: str) -> None:
        """Inject a user/assistant exchange into history without an API call.

        Used by the agent loop so Dash remembers autonomous actions.
        Override in providers that maintain conversation history.
        """
        pass
