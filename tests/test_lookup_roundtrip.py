#!/usr/bin/env python3
"""The lookup round trip, driven by a provider that is not Anthropic.

This is the whole claim of the design, so the test is built to fail if it stops being
true: it uses a hand-written stub provider, and it asserts that the `anthropic` package
was never imported. Lookups have to work on DeepSeek through nano-gpt, and on Ollama, and
on anything else that speaks text -- because the mechanism is a tag, not an API feature.

Run with:  python -m unittest discover -s tests
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.pipeline import Pipeline
from llm.base import LLMProvider
from llm.response_parser import parse_response


class StubProvider(LLMProvider):
    """A minimal provider that returns canned replies and keeps history like a real one.

    Deliberately not a mock of any real SDK: if the lookup path needed anything
    provider-specific, this would not work, which is exactly what is being tested.
    """

    def __init__(self, replies):
        self._replies = list(replies)
        self._history = []
        self.prompts_seen = []

    def chat(self, user_message: str) -> str:
        self.prompts_seen.append(user_message)
        self._history.append({"role": "user", "content": user_message})
        reply = self._replies.pop(0) if self._replies else "(nothing left to say)"
        self._history.append({"role": "assistant", "content": reply})
        return reply

    def generate_once(self, prompt, max_tokens=None, system_prompt=None) -> str:
        return ""

    def reset_history(self) -> None:
        self._history.clear()


def make_pipeline(replies):
    """A Pipeline with only the parts the lookup path touches."""
    pipeline = Pipeline.__new__(Pipeline)
    pipeline.llm = StubProvider(replies)
    pipeline.agent_loop = None
    return pipeline


class LookupRoundTripTests(unittest.TestCase):
    def test_a_lookup_is_answered_and_she_speaks_again(self):
        pipeline = make_pipeline([
            "let me check. [LOOKUP:Coffee Farm]",
            "two hundred thousand bits, plus ten vehicle parts. [CONVO:CONTINUE]",
        ])
        parsed = pipeline._resolve_lookups(
            parse_response("let me check. [LOOKUP:Coffee Farm]"),
            "what does a coffee farm cost",
        )
        self.assertEqual(parsed.lookups, [])
        self.assertIn("two hundred thousand", parsed.text)

    def test_the_real_numbers_reach_the_second_call(self):
        pipeline = make_pipeline([
            "hold on [LOOKUP:Coffee Farm]",
            "got it.",
        ])
        pipeline._resolve_lookups(
            parse_response("hold on [LOOKUP:Coffee Farm]"), "coffee farm?"
        )
        second = pipeline.llm.prompts_seen[-1]
        self.assertIn("Looked up for you", second)
        self.assertIn("200,000 bits", second)      # straight out of the game's own SQL
        self.assertIn("5 Coffee", second)          # produced per tick
        self.assertIn("coffee farm?", second)      # the original question is still there

    def test_two_lookups_in_one_reply_are_both_answered(self):
        pipeline = make_pipeline([
            "[LOOKUP:Coffee Farm] [LOOKUP:Copper]",
            "done.",
        ])
        pipeline._resolve_lookups(
            parse_response("[LOOKUP:Coffee Farm] [LOOKUP:Copper]"), "tell me about both"
        )
        second = pipeline.llm.prompts_seen[-1]
        self.assertIn("Coffee Farm", second)
        self.assertIn("Copper", second)

    def test_history_is_rewound_so_the_request_is_not_remembered(self):
        pipeline = make_pipeline(["[LOOKUP:Mall]", "a mall is three million."])
        pipeline._resolve_lookups(parse_response("[LOOKUP:Mall]"), "what about malls")
        roles = [h["role"] for h in pipeline.llm._history]
        self.assertEqual(roles, ["user", "assistant"])
        # The reply that only asked for numbers must not be in the record.
        self.assertNotIn("[LOOKUP:", pipeline.llm._history[-1]["content"])

    def test_a_warcalc_runs_the_real_simulation(self):
        body = "40 Unicorns/Grid Squares/Shining/12 vs 60 Pegasi/Canopy Lights/Dragon/6"
        pipeline = make_pipeline([f"[WARCALC:{body}]", "you'd lose most of them."])
        pipeline._resolve_lookups(parse_response(f"[WARCALC:{body}]"), "can I win")
        second = pipeline.llm.prompts_seen[-1]
        # The numbers verified against the browser warcalc across 2,000 battles.
        self.assertIn("lost 33", second)
        self.assertIn("lost 45", second)

    def test_a_bad_lookup_comes_back_as_text_not_an_exception(self):
        pipeline = make_pipeline(["[LOOKUP:Cheese Factory]", "no such thing, apparently."])
        parsed = pipeline._resolve_lookups(
            parse_response("[LOOKUP:Cheese Factory]"), "cheese?"
        )
        self.assertIn("error:", pipeline.llm.prompts_seen[-1])
        self.assertIn("no such thing", parsed.text)

    def test_a_model_that_never_stops_asking_is_cut_off(self):
        # Every reply asks again. The loop must end and must not leak the tag to TTS.
        pipeline = make_pipeline(["[LOOKUP:Mall]"] * 6)
        parsed = pipeline._resolve_lookups(parse_response("[LOOKUP:Mall]"), "malls?")
        self.assertEqual(parsed.lookups, [])
        self.assertNotIn("[LOOKUP", parsed.text)
        self.assertLessEqual(len(pipeline.llm.prompts_seen), Pipeline.MAX_LOOKUP_ROUNDS)

    def test_no_lookup_means_no_extra_call(self):
        pipeline = make_pipeline(["a coffee farm is 200k."])
        parsed = parse_response("a coffee farm is 200k.")
        out = pipeline._resolve_lookups(parsed, "cost?")
        self.assertIs(out, parsed)
        self.assertEqual(pipeline.llm.prompts_seen, [])


class NoAnthropicTests(unittest.TestCase):
    def test_the_lookup_path_never_imports_anthropic(self):
        """The point of the rewrite. If this fails, it is provider-locked again."""
        self.assertNotIn("anthropic", sys.modules)


if __name__ == "__main__":
    unittest.main()
