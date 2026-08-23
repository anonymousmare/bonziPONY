#!/usr/bin/env python3
"""Offline unit tests for fourchan.py -- no network."""

import unittest

from fourchan import (
    ThreadParseError,
    is_archived,
    parse_comment_lines,
    parse_thread_payload,
    render_thread_compact,
)


def post(no, com="", **extra):
    row = {"no": no, "time": 1753393662, "name": "Anonymous", "com": com}
    row.update(extra)
    return row


def thread(*posts):
    return {"posts": list(posts)}


class CommentLineTests(unittest.TestCase):
    def test_br_becomes_a_line_break(self):
        self.assertEqual(parse_comment_lines("one<br>two"), "one\ntwo")

    def test_whitespace_collapses_within_a_line_but_not_across(self):
        self.assertEqual(parse_comment_lines("a   b<br>c   d"), "a b\nc d")

    def test_greentext_survives_as_its_own_line(self):
        # This is the whole reason this parser exists alongside
        # clop_monitor.parse_fourchan_comment, which would flatten it to one line.
        text = parse_comment_lines(
            '<span class="quote">&gt;rules</span><br>lol<br><span class="quote">&gt;me</span>'
        )
        self.assertEqual(text, ">rules\nlol\n>me")

    def test_runs_of_blank_lines_collapse_to_one(self):
        self.assertEqual(parse_comment_lines("a<br><br><br><br>b"), "a\n\nb")

    def test_entities_are_decoded(self):
        self.assertEqual(parse_comment_lines("&gt;&gt;123 &amp; co"), ">>123 & co")


class ParseThreadTests(unittest.TestCase):
    def test_posts_are_numbered_from_one_in_thread_order(self):
        posts = parse_thread_payload(thread(post(100, "a"), post(200, "b"), post(300, "c")))
        self.assertEqual([p.sequence for p in posts], [1, 2, 3])
        self.assertEqual([p.number for p in posts], [100, 200, 300])

    def test_subject_is_read_from_the_opening_post(self):
        posts = parse_thread_payload(thread(post(100, "a", sub="CLOP General"), post(200, "b")))
        self.assertEqual(posts[0].subject, "CLOP General")
        self.assertEqual(posts[1].subject, "")

    def test_quotes_record_the_real_post_numbers(self):
        posts = parse_thread_payload(thread(post(100, "a"), post(200, "&gt;&gt;100 yes")))
        self.assertEqual(posts[1].quotes, (100,))

    def test_a_post_with_no_text_is_named_by_its_image(self):
        posts = parse_thread_payload(thread(post(100, "", filename="Apple", ext=".png")))
        self.assertEqual(posts[0].body, "[image: Apple.png]")

    def test_a_post_with_neither_text_nor_image(self):
        posts = parse_thread_payload(thread(post(100, "")))
        self.assertEqual(posts[0].body, "[no text]")

    def test_one_unreadable_post_does_not_cost_the_others(self):
        posts = parse_thread_payload(thread(post(100, "a"), {"no": "junk"}, post(300, "c")))
        self.assertEqual([p.number for p in posts], [100, 300])

    def test_a_payload_that_is_not_a_thread_raises(self):
        with self.assertRaises(ThreadParseError):
            parse_thread_payload({"nope": []})
        with self.assertRaises(ThreadParseError):
            parse_thread_payload(thread())

    def test_url_points_at_the_post(self):
        posts = parse_thread_payload(thread(post(100, "a"), post(200, "b")))
        self.assertEqual(
            posts[1].url("https://boards.4chan.org/mlp/thread/100"),
            "https://boards.4chan.org/mlp/thread/100#p200",
        )


class ArchivedTests(unittest.TestCase):
    def test_archived_opening_post(self):
        self.assertTrue(is_archived(thread(post(100, "a", archived=1))))

    def test_live_thread(self):
        self.assertFalse(is_archived(thread(post(100, "a"))))

    def test_nonsense_is_not_archived(self):
        self.assertFalse(is_archived({}))
        self.assertFalse(is_archived("nope"))


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.posts = parse_thread_payload(thread(
            post(43381621, "Welcome.", sub="CLOP General"),
            post(43381622, "&gt;&gt;43381621<br>lol"),
            post(43381650, "&gt;&gt;99999999 elsewhere"),
        ))

    def test_quotelinks_are_renumbered(self):
        text = render_thread_compact(self.posts)
        self.assertIn(">>1", text)
        self.assertNotIn(">>43381621", text)

    def test_a_link_to_another_thread_is_left_alone(self):
        # There is no sequence number to give it, and guessing would be a lie.
        self.assertIn(">>99999999", render_thread_compact(self.posts))

    def test_subject_appears_on_the_opening_post(self):
        self.assertIn("| CLOP General", render_thread_compact(self.posts))

    def test_since_number_shows_only_newer_posts(self):
        text = render_thread_compact(self.posts, since_number=43381622)
        self.assertIn("elsewhere", text)
        self.assertNotIn("Welcome.", text)

    def test_renumbering_stays_whole_thread_even_when_filtered(self):
        # Post 2 is not rendered, but if it were its ">>1" would still mean post 1.
        # What matters is that the count reflects the whole thread.
        text = render_thread_compact(self.posts, since_number=43381621)
        self.assertIn("3 posts total, 2 shown", text)
        self.assertIn(">>1", text)

    def test_no_new_posts_says_so(self):
        text = render_thread_compact(self.posts, since_number=43381650)
        self.assertIn("(no new posts)", text)

    def test_board_and_thread_id_head_the_render(self):
        text = render_thread_compact(self.posts, board="mlp", thread_id=43381621)
        self.assertTrue(text.startswith("/mlp/ 43381621 - 3 posts total"))

    def test_render_is_much_shorter_than_the_raw_payload(self):
        # The point of the whole module. Renumbering alone takes out nine digits
        # per quote-link, and the HTML wrapper goes entirely.
        raw = str(thread(
            post(43381621, "Welcome.", sub="CLOP General"),
            post(43381622, '<a href="#p43381621" class="quotelink">&gt;&gt;43381621</a><br>lol'),
        ))
        rendered = render_thread_compact(self.posts[:2], board="mlp", thread_id=43381621)
        self.assertLess(len(rendered), len(raw))


if __name__ == "__main__":
    unittest.main()
