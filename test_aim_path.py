import math
import unittest

from aim_path import JumpPath, IdlePath
from aim_physics import AimConfig, AssistPhysics


class JumpPathTests(unittest.TestCase):
    def test_wiggle_preserves_endpoint_position_velocity_and_acceleration(self):
        base = JumpPath((0, 0), (400, 0), (100, 20), (500, 100, 200), 300, .65)
        varied = JumpPath((0, 0), (400, 0), (100, 20), (500, 100, 200), 300, .65, wiggle=.8, seed=1)
        for elapsed in (0, 300):
            self.assertEqual(base.sample(elapsed), varied.sample(elapsed))
            self.assertEqual(base.acceleration(elapsed), varied.acceleration(elapsed))
        self.assertNotEqual(base.sample(150), varied.sample(150))

    def test_wiggle_is_fixed_per_jump_and_differs_across_seeds(self):
        first = JumpPath((0, 0), (400, 0), (0, 0), None, 300, .65, wiggle=.8, seed=1)
        second = JumpPath((0, 0), (400, 0), (0, 0), None, 300, .65, wiggle=.8, seed=2)
        self.assertEqual(first.sample(120), first.sample(120))
        self.assertNotEqual(first.sample(120), second.sample(120))

    def test_multiple_wiggle_families_keep_all_hit_points(self):
        styles = set()
        for seed in range(100):
            path = JumpPath((0,0),(400,0),(0,0),(0,0,200),300,.8,wiggle=.8,seed=seed)
            styles.add(path.wiggle_style)
            self.assertEqual(path.sample(0)[0],(0,0))
            self.assertEqual(path.sample(300)[0],(400,0))
        self.assertEqual(styles, {'arc','sweep','loop','ribbon'})

    def test_wiggle_derivatives_match_the_curve(self):
        path = JumpPath((0, 0), (400, 100), (20, 40), None, 300, .65, wiggle=.8, seed=1)
        for elapsed in (20, 90, 150, 280):
            before, after = path.sample(elapsed-.001), path.sample(elapsed+.001)
            _, velocity = path.sample(elapsed)
            acceleration = path.acceleration(elapsed)
            for axis in (0, 1):
                self.assertAlmostEqual((after[0][axis]-before[0][axis])/.000002, velocity[axis], places=3)
                self.assertAlmostEqual((after[1][axis]-before[1][axis])/.000002, acceleration[axis], places=2)

    def test_zero_wiggle_budget_keeps_original_curve(self):
        base = JumpPath((0, 0), (400, 0), (0, 0), None, 300, .65)
        limited = JumpPath((0, 0), (400, 0), (0, 0), None, 300, .65, wiggle=1, seed=1, wiggle_limit=0)
        self.assertEqual(base.sample(150), limited.sample(150))

    def test_wiggle_following_arrives_without_losing_the_hit_point(self):
        config = AimConfig(strength=1, radius=740, max_offset=640, max_speed=12000,
                           max_accel=750000, require_motion=False, on_note_hold=False)
        physics = AssistPhysics(config)
        path = JumpPath((0, 0), (500, 0), (0, 0), None, 180, 1,
                        wiggle=.8, seed=1, wiggle_limit=64)
        cursor = physics.step((0, 0), (0, 0), 180, 1, 0, 0, 1, following=True)
        for i in range(1, 181):
            target, velocity = path.sample(i)
            cursor = physics.step(cursor, target, 180-i, 1, i/1000, .001, 1,
                                  target_velocity=velocity, target_accel=path.acceleration(i), following=True)
        self.assertLess(math.dist(cursor, (500, 0)), 5)
    def test_idle_path_preserves_entry_velocity_and_zero_initial_acceleration(self):
        path = IdlePath((256, 192), (120, -40), (0, 0, 512, 384), .65)
        self.assertEqual(path.sample(0), ((256, 192), (120, -40), (0, 0)))

    def test_idle_orbit_stays_inside_playfield_after_entry(self):
        path = IdlePath((10, 10), (120, -40), (0, 0, 512, 384), .65)
        for elapsed in range(500, 5000, 10):
            position, velocity, acceleration = path.sample(elapsed)
            self.assertTrue(0 <= position[0] <= 512 and 0 <= position[1] <= 384)
            self.assertTrue(all(math.isfinite(v) for v in (*velocity, *acceleration)))
            self.assertGreater(math.hypot(*velocity), 1)

    def test_idle_velocity_and_acceleration_match_the_path(self):
        path = IdlePath((256, 192), (120, -40), (0, 0, 512, 384), .65)
        for elapsed in (20, 120, 449, 700):
            before, after = path.sample(elapsed-.001), path.sample(elapsed+.001)
            _, velocity, acceleration = path.sample(elapsed)
            for axis in (0, 1):
                self.assertAlmostEqual((after[0][axis]-before[0][axis])/.000002, velocity[axis], places=3)
                self.assertAlmostEqual((after[1][axis]-before[1][axis])/.000002, acceleration[axis], places=2)
    def test_u_turn_has_a_tangent_instead_of_stopping_at_the_note(self):
        path = JumpPath((0, 0), (400, 0), (0, 0), (0, 0, 200), 200, .65)
        self.assertGreater(path.sample(200)[1][0], 100)
        self.assertEqual(path.sample(200)[0], (400, 0))
        self.assertEqual(path.acceleration(200), (0, 0))

    def test_row_reversal_crosses_note_then_rounds_back_continuously(self):
        first = JumpPath((0, 0), (400, 0), (800, 0), (0, 100, 400), 400, 1)
        position, velocity = first.sample(400)
        second = JumpPath(position, (0, 100), velocity, (400, 100, 400), 400, 1)
        self.assertGreater(velocity[0], abs(velocity[1]))
        self.assertGreater(math.hypot(*velocity), 1000)
        self.assertEqual(second.sample(0), (position, velocity))
        self.assertGreater(second.sample(20)[0][0], 400)
        self.assertEqual(second.sample(400)[0], (0, 100))
        self.assertEqual(first.acceleration(400), second.acceleration(0))
    def test_endpoints_and_initial_velocity(self):
        path = JumpPath((10, 20), (410, 220), (100, 50), (450, 340, 200), 180, .65)
        self.assertEqual(path.sample(0), ((10, 20), (100, 50)))
        self.assertEqual(path.sample(180)[0], (410, 220))
        self.assertEqual(path.sample(900)[0], (410, 220))
        self.assertEqual(path.sample(-10)[0], (10, 20))
        self.assertEqual(path.acceleration(0), (0, 0))
        self.assertEqual(path.acceleration(180), (0, 0))

    def test_curve_opening_and_asymmetry_follow_spacing_and_tempo(self):
        straight = JumpPath((0, 0), (400, 0), (500, 0), (800, 0, 400), 400, 1)
        turn = JumpPath((0, 0), (400, 0), (500, 0), (400, 400, 100), 400, 1)
        self.assertGreater(abs(turn.bow), abs(straight.bow))
        self.assertGreater(turn.bow_skew, 0)
        self.assertEqual(straight.bow_skew, 0)
        self.assertGreater(turn._bow(.7)[0], turn._bow(.3)[0])
        for elapsed in (20, 110, 230, 380):
            before, after = turn.sample(elapsed-.001), turn.sample(elapsed+.001)
            _, velocity = turn.sample(elapsed)
            acceleration = turn.acceleration(elapsed)
            for axis in (0, 1):
                self.assertAlmostEqual((after[0][axis]-before[0][axis])/.000002, velocity[axis], places=3)
                self.assertAlmostEqual((after[1][axis]-before[1][axis])/.000002, acceleration[axis], places=2)
        self.assertEqual(turn.sample(400)[0], (400, 0))
        self.assertEqual(turn.acceleration(400), (0, 0))

    def test_curvature_responds_to_next_note_and_can_be_disabled(self):
        up = JumpPath((0, 0), (400, 0), (0, 0), (400, 100, 100), 200, .8)
        down = JumpPath((0, 0), (400, 0), (0, 0), (400, -100, 100), 200, .8)
        self.assertGreater(up.sample(100)[0][1], 0)
        self.assertLess(down.sample(100)[0][1], 0)
        straight = JumpPath((0, 0), (400, 0), (0, 0), None, 200, 0)
        self.assertEqual(straight.sample(100)[0][1], 0)

    def test_reported_velocity_matches_curve_derivative(self):
        path = JumpPath((10, 20), (410, 220), (100, 50), (450, 340, 200), 180, .65)
        for elapsed in (1, 30, 90, 160, 179):
            before, after = path.sample(elapsed - .001)[0], path.sample(elapsed + .001)[0]
            velocity = path.sample(elapsed)[1]
            acceleration = path.acceleration(elapsed)
            before_v, after_v = path.sample(elapsed - .001)[1], path.sample(elapsed + .001)[1]
            for axis in (0, 1):
                self.assertAlmostEqual((after[axis] - before[axis]) / .000002, velocity[axis], places=3)
                self.assertAlmostEqual((after_v[axis] - before_v[axis]) / .000002, acceleration[axis], places=2)

    def test_long_jump_arrives_with_curvature_and_bounded_acceleration(self):
        config = AimConfig(strength=1, radius=640, max_offset=640, max_speed=12000,
                           max_accel=750000, require_motion=False, on_note_hold=False)
        physics = AssistPhysics(config)
        path = JumpPath((0, 0), (500, 0), (0, 0), None, 180, .65)
        cursor = physics.step((0, 0), (0, 0), 180, 1, 0, 0, 1, following=True)
        positions = []
        for i in range(1, 181):
            target, velocity = path.sample(i)
            old_velocity = physics.velocity
            cursor = physics.step(cursor, target, 180 - i, 1, i / 1000, .001, 1,
                                  target_velocity=velocity, following=True, target_accel=path.acceleration(i))
            positions.append(cursor)
            self.assertLessEqual(math.dist(old_velocity, physics.velocity), config.max_accel * .001 + 1e-6)
        self.assertLess(math.dist(cursor, (500, 0)), 5)
        self.assertGreater(max(y for x, y in positions), 20)
        self.assertEqual(physics.raw_position, (0, 0))

    def test_fast_long_jump_arrival(self):
        config = AimConfig(strength=1, radius=640, max_offset=640, max_speed=12000,
                           max_accel=750000, require_motion=False, on_note_hold=False)
        physics = AssistPhysics(config)
        path = JumpPath((0, 0), (600, 0), (0, 0), None, 100, .65)
        cursor = physics.step((0, 0), (0, 0), 100, 1, 0, 0, 1, following=True)
        for i in range(1, 101):
            target, velocity = path.sample(i)
            cursor = physics.step(cursor, target, 100-i, 1, i/1000, .001, 1,
                following=True, target_velocity=velocity, target_accel=path.acceleration(i))
        self.assertLess(math.dist(cursor, (600, 0)), 5)


if __name__ == '__main__':
    unittest.main()
