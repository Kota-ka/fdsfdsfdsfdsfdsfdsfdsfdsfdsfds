import unittest

from stacking import stack_heights
from slider_path import SliderPath


def circle(time, x=0, y=0):
    return dict(time=time, x=x, y=y, type=1, duration=0)


def slider(time=0, repeats=1):
    return dict(time=time, x=0, y=0, type=2, duration=100,
                slider_path=SliderPath((0, 0), 'L|100:0', 100), slider_repeats=repeats)


class StackingTests(unittest.TestCase):
    def test_circle_chain(self):
        objects = [circle(0), circle(100), circle(200)]
        self.assertEqual(stack_heights(objects, 150), [2, 1, 0])
        self.assertEqual(objects[0]['x'], 0)

    def test_time_and_distance_limits(self):
        self.assertEqual(stack_heights([circle(0), circle(500)], 150), [0, 0])
        self.assertEqual(stack_heights([circle(0), circle(100, 3)], 150), [0, 0])

    def test_circles_below_slider_tail_stack_negative(self):
        self.assertEqual(stack_heights([slider(), circle(150, 100), circle(200, 100)], 150), [0, -1, -2])

    def test_even_repeat_ends_back_at_slider_head(self):
        self.assertEqual(stack_heights([slider(repeats=2), circle(150)], 150), [0, -1])

    def test_slider_on_previous_tail(self):
        following = slider(150)
        following['x'] = 100
        following['slider_path'] = SliderPath((100, 0), 'L|200:0', 100)
        self.assertEqual(stack_heights([slider(), following], 200), [1, 0])

    def test_legacy_format(self):
        self.assertEqual(stack_heights([circle(0), circle(100), circle(200)], 150, 5), [2, 1, 0])

    def test_spinners_have_no_stack(self):
        spinner = dict(time=50, x=0, y=0, duration=20, type=8)
        self.assertEqual(stack_heights([circle(0), spinner, circle(100)], 150), [1, 0, 0])
