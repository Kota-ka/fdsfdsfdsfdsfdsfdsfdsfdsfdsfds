import math
import unittest

from slider_path import SliderPath, slider_motion


class SliderTests(unittest.TestCase):
    def test_compact_slider_does_not_chase_tiny_reversals(self):
        path = SliderPath((0,0),'L|5:0',5)
        for elapsed in range(0,1000,10):
            position, velocity, acceleration = slider_motion(path,elapsed,1000,20,24,soften=True,relax_radius=31)
            self.assertEqual(position,(0,0))
            self.assertEqual(velocity,(0,0))
            self.assertEqual(acceleration,(0,0))
    def test_slider_drift_is_broad_bounded_and_returns_to_endpoints(self):
        path = SliderPath((0,0),'L|100:0',100)
        for repeats in (1,2,3):
            positions = [slider_motion(path,t,1000,repeats,4,drift=35)[0] for t in range(1001)]
            self.assertEqual(positions[0],path.position(0,1000,repeats))
            self.assertEqual(positions[-1],path.position(1000,1000,repeats))
            self.assertGreater(max(p[1] for p in positions),25)
            self.assertLessEqual(max(abs(p[1]) for p in positions),35)
            self.assertLess(max(abs(positions[i+1][1]-positions[i][1]) for i in range(1000)),.3)

    def test_repeat_tangent_changes_gradually_without_changing_path_position(self):
        path = SliderPath((0, 0), 'L|100:0', 100)
        for elapsed, direction in ((95, 1), (100, 0), (105, -1)):
            position, velocity, acceleration = slider_motion(path, elapsed, 200, 2, 8)
            self.assertEqual(position, path.position(elapsed, 200, 2))
            self.assertEqual((velocity[0] > 0) - (velocity[0] < 0), direction)
            self.assertLess(acceleration[0], 0)
            self.assertLessEqual(abs(velocity[0]), 1000)

    def test_motion_sampling_is_safe_at_slider_endpoints(self):
        path = SliderPath((0, 0), 'L|100:0', 100)
        for elapsed in (0, 200):
            position, velocity, acceleration = slider_motion(path, elapsed, 200, 1, 8)
            self.assertEqual(position, path.position(elapsed, 200, 1))
            self.assertAlmostEqual(velocity[0], 500)
            self.assertEqual(acceleration, (0, 0))

    def test_soft_repeat_rounds_the_tip_within_a_small_distance(self):
        path = SliderPath((0,0),'L|100:0',100)
        position, velocity, acceleration = slider_motion(path,100,200,2,16,soften=True)
        self.assertEqual(position,(92,0))
        self.assertAlmostEqual(velocity[0],0)
        self.assertLess(acceleration[0],0)

    def test_elastic_slider_replay_remains_close_to_the_game_path(self):
        from aim_physics import AimConfig,AssistPhysics
        path = SliderPath((0,0),'L|100:0',100)
        physics = AssistPhysics(AimConfig(strength=1,damping_ratio=.6,require_motion=False,
            on_note_hold=False,max_speed=16000,max_accel=1000000,max_offset=640))
        cursor = physics.step((0,0),(0,0),0,31,0,0,1,tracking=True)
        maximum_error = 0
        for elapsed in range(1,401):
            position, velocity, acceleration = slider_motion(path,elapsed,400,2,24,soften=True)
            cursor = physics.step(cursor,position,0,31,elapsed/1000,.001,1,tracking=True,
                                  target_velocity=velocity,target_accel=acceleration)
            maximum_error = max(maximum_error,math.dist(cursor,path.position(elapsed,400,2)))
        self.assertLess(maximum_error,31*.5)
    def test_linear_repeats_and_endpoint_parity(self):
        path = SliderPath((0, 0), 'L|100:0', 100)
        for elapsed, expected in ((0, 0), (250, 50), (500, 100), (750, 50), (1000, 0)):
            self.assertEqual(path.position(elapsed, 1000, 2), (expected, 0))
        self.assertEqual(path.position(1000, 1000, 3), (100, 0))

    def test_declared_length_trims_and_extends_path(self):
        self.assertEqual(SliderPath((0, 0), 'L|100:0', 50).position(1, 1, 1), (50, 0))
        self.assertEqual(SliderPath((0, 0), 'L|100:0', 150).position(1, 1, 1), (150, 0))

    def test_bezier_with_repeated_control_has_a_corner(self):
        path = SliderPath((0, 0), 'B|50:0|50:0|50:50', 100)
        self.assertEqual(path.position(500, 1000, 1), (50, 0))
        self.assertEqual(path.position(1000, 1000, 1), (50, 50))

    def test_bezier_is_a_curve_not_just_control_polygon(self):
        path = SliderPath((0, 0), 'B|50:100|100:0', 100)
        x, y = path.position(500, 1000, 1)
        self.assertGreater(y, 20)
        self.assertLess(y, 50)
        self.assertLess(x, 50)

    def test_perfect_arc_goes_through_middle_control(self):
        path = SliderPath((1, 0), 'P|0:1|-1:0', math.pi)
        x, y = path.position(500, 1000, 1)
        self.assertAlmostEqual(x, 0, delta=.001)
        self.assertAlmostEqual(y, 1, delta=.001)

    def test_catmull_and_degenerate_arc_are_supported(self):
        path = SliderPath((0, 0), 'C|50:50|100:0', 100)
        self.assertTrue(all(math.isfinite(v) for v in path.position(500, 1000, 1)))
        self.assertEqual(SliderPath((0, 0), 'P|50:0|100:0', 100).position(1, 1, 1), (100, 0))

    def test_invalid_paths_are_rejected(self):
        for encoded, length in (('L|nan:0', 100), ('L|0:0', 100), ('Q|100:0', 100), ('L|100:0', -1)):
            with self.subTest(encoded=encoded), self.assertRaises(ValueError):
                SliderPath((0, 0), encoded, length)


if __name__ == '__main__':
    unittest.main()
