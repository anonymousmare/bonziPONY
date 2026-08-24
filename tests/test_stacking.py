"""Where the notification box lands when the pony is talking.

``desktop_pet.stacking`` is the placement maths with the Qt taken out, so this can assert on
coordinates rather than on a screenshot. The scenario throughout is the real one: a pony pinned
to the bottom-right, a speech bubble above her, and an alert arriving mid-sentence.
"""

from __future__ import annotations

import unittest

from desktop_pet.stacking import GAP, intersects, place, stack_above, stack_below, union

#: A 1920x1080 screen with the taskbar taken off the bottom, as availableGeometry returns.
SCREEN = (0, 0, 1920, 1040)
#: Twilight, pinned bottom-right.
PONY = (1700, 800, 200, 240)
#: Her speech bubble: above her, wider than she is.
BUBBLE = (1600, 700, 320, 90)


def covers(a, b) -> bool:
    return intersects(a, b)


class StackAboveTests(unittest.TestCase):
    def test_free_space_is_left_alone(self):
        self.assertEqual(stack_above([], 0, 100, 50, 400, 0), 400)

    def test_hops_over_one_obstacle(self):
        rects = [(0, 300, 100, 100)]           # occupies y 300..400
        y = stack_above(rects, 0, 100, 50, 380, 0)
        self.assertEqual(y, 300 - GAP - 50)

    def test_hops_over_a_stack_of_two(self):
        rects = [(0, 300, 100, 100), (0, 200, 100, 60)]
        y = stack_above(rects, 0, 100, 50, 380, 0)
        self.assertEqual(y, 200 - GAP - 50)
        for rect in rects:
            self.assertFalse(covers((0, y, 100, 50), rect))

    def test_gives_up_at_the_top_of_the_screen(self):
        rects = [(0, 0, 100, 900)]
        self.assertIsNone(stack_above(rects, 0, 100, 50, 880, 0))

    def test_ignores_what_is_not_in_the_column(self):
        rects = [(500, 300, 100, 100)]         # off to the side
        self.assertEqual(stack_above(rects, 0, 100, 50, 380, 0), 380)


class StackBelowTests(unittest.TestCase):
    def test_hops_under_one_obstacle(self):
        rects = [(0, 300, 100, 100)]
        y = stack_below(rects, 0, 100, 50, 320, 1000)
        self.assertEqual(y, 400 + GAP)

    def test_gives_up_at_the_bottom(self):
        rects = [(0, 300, 100, 100)]
        self.assertIsNone(stack_below(rects, 0, 100, 50, 320, 420))


class PlaceTests(unittest.TestCase):
    BOX = (340, 120)      # width, height, about what a two-line alert measures

    def place(self, obstacles):
        w, h = self.BOX
        x, y = place(w, h, PONY, obstacles, SCREEN)
        return (x, y, w, h)

    def test_sits_above_the_pony_when_she_is_quiet(self):
        box = self.place([PONY])
        self.assertFalse(covers(box, PONY))
        self.assertEqual(box[1], PONY[1] - GAP - self.BOX[1])
        # She is in the corner and the box is wider than she is, so it is against the
        # screen edge rather than centred on her.
        self.assertEqual(box[0] + box[2], SCREEN[0] + SCREEN[2])

    def test_centres_on_the_pony_when_there_is_room(self):
        middle = (900, 800, 200, 240)
        w, h = self.BOX
        x, y = place(w, h, middle, [middle], SCREEN)
        self.assertAlmostEqual(x + w / 2, middle[0] + middle[2] / 2, delta=1)

    def test_moves_up_when_she_starts_talking(self):
        quiet = self.place([PONY])
        talking = self.place([PONY, BUBBLE])
        self.assertFalse(covers(talking, BUBBLE))
        self.assertFalse(covers(talking, PONY))
        self.assertLess(talking[1], quiet[1])
        self.assertEqual(talking[1], BUBBLE[1] - GAP - self.BOX[1])

    def test_drops_back_when_she_stops(self):
        """The bubble auto-hides; the box must come back down rather than stay stranded."""
        self.assertEqual(self.place([PONY, BUBBLE]) != self.place([PONY]), True)
        self.assertEqual(self.place([PONY])[1], PONY[1] - GAP - self.BOX[1])

    def test_stays_on_screen_horizontally(self):
        box = self.place([PONY])
        self.assertGreaterEqual(box[0], SCREEN[0])
        self.assertLessEqual(box[0] + box[2], SCREEN[0] + SCREEN[2])

    def test_falls_below_when_there_is_no_room_above(self):
        """A pony at the top of the screen has nowhere above her."""
        top_pony = (1700, 0, 200, 240)
        w, h = self.BOX
        x, y = place(w, h, top_pony, [top_pony], SCREEN)
        self.assertFalse(covers((x, y, w, h), top_pony))
        self.assertEqual(y, top_pony[1] + top_pony[3] + GAP)

    def test_goes_beside_when_there_is_no_room_either_way(self):
        tall = (900, 0, 200, 1040)             # a pony as tall as the screen
        w, h = self.BOX
        x, y = place(w, h, tall, [tall], SCREEN)
        self.assertFalse(covers((x, y, w, h), tall))
        self.assertEqual(x, tall[0] + tall[2] + GAP)

    def test_goes_beside_on_the_other_side_when_the_right_is_off_screen(self):
        tall = (1700, 0, 200, 1040)            # tall, and hard against the right edge
        w, h = self.BOX
        x, y = place(w, h, tall, [tall], SCREEN)
        self.assertFalse(covers((x, y, w, h), tall))
        self.assertEqual(x, tall[0] - GAP - w)
        self.assertGreaterEqual(x, SCREEN[0])

    def test_never_loops_forever_on_overlapping_obstacles(self):
        """Obstacles that overlap each other resolve in bounded passes, not a hang."""
        pile = [(1700, 800 - 60 * i, 200, 100) for i in range(8)]
        box = self.place([PONY] + pile)
        for rect in pile:
            self.assertFalse(covers(box, rect))

    def test_survives_a_screen_it_was_not_given(self):
        w, h = self.BOX
        x, y = place(w, h, PONY, [PONY], None)
        self.assertFalse(covers((x, y, w, h), PONY))

    def test_overlaps_as_a_last_resort_rather_than_failing(self):
        """Nowhere to go: still a position, still on screen, no exception."""
        everywhere = [(0, 0, 1920, 1040)]
        w, h = self.BOX
        x, y = place(w, h, PONY, everywhere, SCREEN)
        self.assertGreaterEqual(y, SCREEN[1])
        self.assertLessEqual(y + h, SCREEN[1] + SCREEN[3])


class GeometryTests(unittest.TestCase):
    def test_edges_touching_is_not_overlapping(self):
        self.assertFalse(intersects((0, 0, 10, 10), (10, 0, 10, 10)))
        self.assertFalse(intersects((0, 0, 10, 10), (0, 10, 10, 10)))
        self.assertTrue(intersects((0, 0, 10, 10), (9, 9, 10, 10)))

    def test_union_covers_both(self):
        self.assertEqual(union([(0, 0, 10, 10), (20, 5, 10, 10)]), (0, 0, 30, 15))


if __name__ == "__main__":
    unittest.main()
