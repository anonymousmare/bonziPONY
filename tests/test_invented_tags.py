#!/usr/bin/env python3
"""A command she made up must never be spoken, and must never end the turn silently.

Observed in the wild:

    [BROWSE:4chan.org/mlp/] sure, let's see what's going on in the thread. one sec.
    [CONVO:CONTINUE]

Two failures in one line. `[CONVO:CONTINUE]` was stripped because it is a real tag, but
`[BROWSE:...]` was read out loud, because the leftover-tag pattern is an allowlist of tags
that exist and BROWSE is not one of them. And because nothing ran, "one sec" was the entire
turn -- she promised to check something and then stopped.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "clop_monitor"))

from llm.response_parser import parse_response

OBSERVED = ("[BROWSE:4chan.org/mlp/] sure, let's see what's going on in the thread. "
            "one sec. [CONVO:CONTINUE]")


class StrippingTests(unittest.TestCase):
    def test_the_observed_reply_does_not_speak_the_tag(self):
        parsed = parse_response(OBSERVED)
        self.assertNotIn("BROWSE", parsed.text)
        self.assertNotIn("[", parsed.text)
        self.assertEqual("sure, let's see what's going on in the thread. one sec.",
                         parsed.text)

    def test_the_invented_tag_is_reported_rather_than_just_dropped(self):
        # The pipeline needs to know, or it cannot tell this apart from a normal reply.
        self.assertEqual(["BROWSE"], parse_response(OBSERVED).unknown_tags)

    def test_a_real_tag_is_not_mistaken_for_an_invented_one(self):
        parsed = parse_response("sitting down now. [ACTION:SIT] [CONVO:CONTINUE]")
        self.assertEqual([], parsed.unknown_tags)
        self.assertEqual("sitting down now.", parsed.text)

    def test_several_invented_tags_are_all_caught(self):
        parsed = parse_response("[SEARCH:prices] [BROWSE:x] [TOOL_CALL:y] checking")
        self.assertEqual(["SEARCH", "BROWSE", "TOOL_CALL"], parsed.unknown_tags)
        self.assertEqual("checking", parsed.text)

    def test_a_bare_invented_tag_with_no_arguments_is_caught(self):
        parsed = parse_response("[THINKING] hmm")
        self.assertEqual(["THINKING"], parsed.unknown_tags)
        self.assertEqual("hmm", parsed.text)

    def test_ordinary_bracketed_prose_is_left_alone(self):
        # Only shouting counts as a tag, so her actual words survive.
        parsed = parse_response("I was [thinking] about the [gym] thing")
        self.assertEqual([], parsed.unknown_tags)
        self.assertIn("thinking", parsed.text)

    def test_a_real_lookup_is_not_flagged(self):
        parsed = parse_response("[LOOKUP:thread]")
        self.assertEqual(["thread"], parsed.lookups)
        self.assertEqual([], parsed.unknown_tags)


class RecoveryTests(unittest.TestCase):
    """The pipeline turns a dead 'one sec' into a real answer."""

    def setUp(self):
        from core.pipeline import Pipeline

        self.replies = []
        self.asked = []
        outer = self

        class Provider:
            _history = [{"role": "user", "content": "what's the thread saying"},
                        {"role": "assistant", "content": OBSERVED}]

            def chat(self, message):
                outer.asked.append(message)
                self._history.append({"role": "user", "content": message})
                reply = outer.replies.pop(0)
                self._history.append({"role": "assistant", "content": reply})
                return reply

        self.pipeline = Pipeline.__new__(Pipeline)
        self.pipeline.llm = Provider()
        self.pipeline.agent_loop = None

    def _recover(self, *replies):
        self.replies = list(replies)
        return self.pipeline._recover_invented_tag(
            parse_response(OBSERVED), "what's the thread saying")

    def test_she_is_told_the_command_does_not_exist(self):
        self._recover("no thread access, sorry.")
        prompt = self.asked[0]
        self.assertIn("[BROWSE:...]", prompt)
        self.assertIn("not a command", prompt)

    def test_she_is_shown_what_she_actually_has(self):
        self._recover("no thread access, sorry.")
        self.assertIn("[LOOKUP:", self.asked[0])

    def test_the_dead_turn_is_replaced_by_the_real_answer(self):
        recovered = self._recover("the thread is arguing about DNA pricing.")
        self.assertEqual("the thread is arguing about DNA pricing.", recovered.text)
        self.assertNotIn("one sec", recovered.text)

    def test_the_dead_reply_is_rewound_out_of_history(self):
        # She should not later see herself having said "one sec" and assume she already
        # answered. The recovery prompt mentions BROWSE on purpose; her reply must not.
        self._recover("answering properly now.")
        assistant = [t["content"] for t in self.pipeline.llm._history
                     if t["role"] == "assistant"]
        self.assertNotIn(OBSERVED, assistant)
        self.assertEqual(["answering properly now."], assistant)

    def test_inventing_a_tag_twice_does_not_loop(self):
        # One recovery round and no more: two dead turns is worse than one vague answer.
        recovered = self._recover("[BROWSE:again] still trying")
        self.assertEqual(1, len(self.asked))
        self.assertNotIn("BROWSE", recovered.text)


if __name__ == "__main__":
    unittest.main()
