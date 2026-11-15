import unittest
import math

from aim_physics import AIM_PROFILES, AimConfig, AssistPhysics


class PhysicsTests(unittest.TestCase):
    def test_elastic_spring_has_controlled_overshoot_in_flight(self):
        physics = AssistPhysics(AimConfig(strength=1, damping_ratio=.6, radius=1000,
            max_offset=640, max_speed=20000, max_accel=1000000, require_motion=False, on_note_hold=False))
        cursor = physics.step((0, 0), (50, 0), 200, 1, 0, 0, 1, following=True)
        positions = []
        for i in range(150):
            cursor = physics.step(cursor, (50, 0), 200, 1, (i+1)*.001, .001, 1, following=True)
            positions.append(physics.hand[0]+physics.offset[0])
        self.assertGreater(max(positions), 51)
        self.assertLess(max(positions), 60)

    def test_arrival_increases_damping_for_precision(self):
        physics = AssistPhysics(AimConfig(strength=1, damping_ratio=.6, radius=1000,
            max_offset=640, max_speed=20000, max_accel=1000000, require_motion=False, on_note_hold=False))
        cursor = physics.step((0, 0), (50, 0), 0, 1, 0, 0, 1, following=True)
        for i in range(150):
            cursor = physics.step(cursor, (50, 0), 0, 1, (i+1)*.001, .001, 1, following=True)
            self.assertLessEqual(physics.hand[0]+physics.offset[0], 50+1e-8)

    def test_gap_preserves_momentum_and_decelerates_without_rewind(self):
        physics = AssistPhysics(AimConfig(coast_ms=150, require_motion=False))
        cursor = physics.step((100, 100), None, 0, 1, 0, 0, 1)
        physics.velocity = (100, 0)
        positions = []
        for i in range(10):
            cursor = physics.step(cursor, None, 0, 1, (i+1)*.01, .01, 1)
            positions.append(cursor[0])
        self.assertGreater(positions[0], 100)
        self.assertEqual(positions, sorted(positions))
        self.assertGreater(physics.velocity[0], 0)
        self.assertLess(physics.velocity[0], 100)
        self.assertAlmostEqual(physics.hand[0]+physics.offset[0], 100+15*(1-math.exp(-.1/.15)))

    def test_coasting_respects_acceleration_and_manual_escape(self):
        physics = AssistPhysics(AimConfig(coast_ms=100, max_accel=100, require_motion=False))
        physics.step((100, 100), None, 0, 1, 0, 0, 1)
        physics.velocity = (1000, 0)
        physics.step((100, 100), None, 0, 1, .001, .001, 1)
        self.assertGreaterEqual(physics.velocity[0], 999.9-1e-6)
        cursor = physics.last_output
        self.assertEqual(physics.step((cursor[0]-2,cursor[1]), None, 0, 1, .002, .001, 1), (cursor[0]-2,cursor[1]))
        self.assertEqual(physics.velocity, (0, 0))

    def test_small_hand_jitter_does_not_cut_inertia_to_zero(self):
        physics = AssistPhysics(AimConfig(coast_ms=150, require_motion=False))
        physics.step((100,100), None, 0, 1, 0, 0, 1)
        physics.velocity = (200,0)
        physics.step((99,100), None, 0, 1, .01, .01, 1)
        self.assertGreater(physics.velocity[0], 0)
        self.assertEqual(physics.reason, 'inertial glide')

    def test_hand_takes_over_gap_in_any_direction_without_scaled_input(self):
        for motion in ((3, 0), (0, 3), (-3, 0)):
            with self.subTest(motion=motion):
                physics = AssistPhysics(AimConfig(coast_ms=150, require_motion=False, hand_gain=.1))
                physics.step((100,100), None, 0, 1, 0, 0, 1)
                physics.velocity = (8000,0)
                cursor = (100+motion[0],100+motion[1])
                self.assertEqual(physics.step(cursor, None, 0, 1, .001, .001, 1), cursor)
                self.assertEqual(physics.velocity, (0,0))
    def test_360_degree_setting_has_full_coverage_without_breaking_180(self):
        outputs = []
        for cone in (180, 360):
            physics = AssistPhysics(AimConfig(cone_deg=cone, require_motion=False, on_note_hold=False))
            cursor = physics.step((100, 100), (150, 100), 0, 1, 0, 0, 1)
            for i in range(10):
                cursor = physics.step((cursor[0]-1, cursor[1]), (150, 100), 0, 1, i*.005, .005, 1)
            outputs.append(cursor)
        self.assertEqual(outputs[0], outputs[1])
    def test_slider_does_not_zero_momentum_at_an_intermediate_waypoint(self):
        physics = AssistPhysics(AimConfig(require_motion=False))
        physics.step((10, 0), (10.01, 0), 0, 1, 0, 0, 1, tracking=True)
        physics.velocity = (220, 0)
        physics.step((10, 0), (10.01, 0), 0, 1, .001, .001, 1,
                     tracking=True, target_velocity=(200, 0))
        self.assertGreater(physics.velocity[0], 200)

    def test_hand_gain_preserves_raw_motion_but_reduces_active_microadjustment(self):
        for gain, expected in ((1, 110), (.25, 102.5)):
            physics = AssistPhysics(AimConfig(hand_gain=gain, cone_deg=180,
                require_motion=False, on_note_hold=False))
            physics.step((100, 100), (150, 100), 0, 1, 0, 0, 1)
            physics.step((110, 100), (150, 100), 0, 1, 0, 0, 1)
            self.assertEqual(physics.raw_position, (110, 100))
            self.assertAlmostEqual(physics.hand[0] + physics.offset[0], expected)

    def test_hand_gain_does_not_restrict_mouse_outside_assistance(self):
        for target, timing, strength in ((None, 0, 1), ((150, 100), 1000, 1),
                                         ((1000, 100), 0, 1), ((150, 100), 0, 0)):
            physics = AssistPhysics(AimConfig(hand_gain=.25, cone_deg=180, strength=strength,
                require_motion=False, on_note_hold=False))
            physics.step((100, 100), target, timing, 1, 0, 0, 1)
            self.assertEqual(physics.step((110, 100), target, timing, 1, .005, .005, 1), (110, 100))

    def test_slider_prediction_reduces_lag_without_generating_hand_input(self):
        errors = []
        for predictive in (False, True):
            physics = AssistPhysics(AimConfig(strength=1, smooth_ms=65, max_offset=128,
                max_speed=2400, max_accel=40000, return_close_ms=60,
                require_motion=False, on_note_hold=False))
            cursor = physics.step((0, 0), (0, 0), 0, 1, 0, 0, 1, tracking=True)
            for i in range(1, 501):
                cursor = physics.step(cursor, (i * .2, 0), 0, 1, i * .001, .001, 1,
                    tracking=True, target_velocity=(200, 0) if predictive else (0, 0))
            errors.append(abs(100 - cursor[0]))
            self.assertEqual(physics.raw_position, (0, 0))
        self.assertLessEqual(errors[1], 1)
        self.assertGreater(errors[0], errors[1] + 3)

    def test_full_slider_tracking_preserves_physical_microadjustment(self):
        physics = AssistPhysics(AimConfig(strength=1, cone_deg=180, require_motion=False))
        cursor = physics.step((0, 0), (50, 0), 0, 1, 0, 0, 1, tracking=True)
        cursor = physics.step((cursor[0] + 2, cursor[1] + 1), (50, 0), 0, 1,
                              .005, .005, 1, tracking=True, target_velocity=(100, 0))
        self.assertEqual(physics.raw_position, (2, 1))
        self.assertGreater(physics.velocity[0], 0)
        self.assertEqual(physics.reason, 'slider tracking')

    def test_full_angle_does_not_cancel_spring_on_small_away_movements(self):
        physics = AssistPhysics(AimConfig(strength=1, cone_deg=180,
            require_motion=False, on_note_hold=False))
        cursor = physics.step((100, 100), (150, 100), 0, 1, 0, 0, 1)
        for i in range(20):
            cursor = physics.step((cursor[0] - 1, cursor[1]), (150, 100),
                                  0, 1, (i + 1) * .005, .005, 1)
            self.assertEqual(physics.reason, 'assisting')
        self.assertGreater(physics.velocity[0], 0)

    def test_spring_moves_progressively_without_overshooting_a_fixed_target(self):
        physics = AssistPhysics(AimConfig(strength=1, smooth_ms=65,
            max_speed=20000, max_accel=100000, max_offset=128,
            require_motion=False, on_note_hold=False))
        cursor = physics.step((0, 0), (50, 0), 0, 1, 0, 0, 1)
        positions = []
        for i in range(500):
            cursor = physics.step(cursor, (50, 0), 0, 1, (i + 1) * .001, .001, 1)
            positions.append(physics.hand[0] + physics.offset[0])
        self.assertLess(positions[0], .2)
        self.assertGreater(positions[30], 10)
        self.assertGreater(positions[-1], 49)
        self.assertLessEqual(max(positions), 50 + 1e-8)
        self.assertTrue(all(b >= a for a, b in zip(positions, positions[1:])))

    def test_one_ms_smoothing_cannot_bypass_speed_limit_after_a_slow_frame(self):
        physics = AssistPhysics(AimConfig(strength=1, smooth_ms=1,
            max_speed=100, max_accel=100000, require_motion=False, on_note_hold=False))
        physics.step((0, 0), (100, 0), 0, 1, 0, 0, 1)
        cursor = physics.step((0, 0), (100, 0), 0, 1, .032, .032, 1)
        self.assertLessEqual(cursor[0], 4)

    def test_full_strength_is_not_attenuated_early_in_capture_window(self):
        def response(time_until):
            physics = AssistPhysics(AimConfig(strength=1, smooth_ms=1,
                max_speed=20000, max_accel=100000, require_motion=False, on_note_hold=False))
            cursor = physics.step((0, 0), (50, 0), time_until, 1, 0, 0, 1)
            for i in range(20):
                cursor = physics.step(cursor, (50, 0), time_until, 1, (i + 1) * .001, .001, 1)
            return cursor
        self.assertEqual(response(200), response(0))
        self.assertGreater(response(200)[0], 10)

    def test_all_profiles_require_hand_motion_and_bound_the_correction(self):
        for name in AIM_PROFILES:
            with self.subTest(profile=name):
                config = AimConfig(**AIM_PROFILES[name])
                physics = AssistPhysics(config)
                cursor = physics.step((0, 0), (100, 0), 0, 1, 0, 0, 1)
                for i in range(30):
                    cursor = physics.step(cursor, (100, 0), 0, 1, (i + 1) * .005, .005, 1)
                    self.assertEqual(cursor, (0, 0))
                for i in range(30):
                    cursor = physics.step((cursor[0] + 1, cursor[1]), (100, 0), 0, 1, 1 + i * .005, .005, 1)
                    self.assertLessEqual(abs(physics.offset[0]), config.max_offset)
                self.assertGreater(physics.offset[0], 0)

    def test_target_change_has_bounded_acceleration(self):
        config = AimConfig(require_motion=False, on_note_hold=False)
        physics = AssistPhysics(config)
        physics.step((0, 0), (50, 0), 0, 1, 0, 0, 1)
        cursor = (0, 0)
        old_velocity = (0, 0)
        for i in range(40):
            target = (50, 0) if i < 20 else (-50, 0)
            cursor = physics.step(cursor, target, 0, 1, (i + 1) * .005, .005, 1)
            change = sum((physics.velocity[k] - old_velocity[k]) ** 2 for k in (0, 1)) ** .5
            self.assertLessEqual(change, config.max_accel * .005 + 1e-6)
            old_velocity = physics.velocity

    def test_slider_corrects_laterally_without_driving_forward(self):
        physics = AssistPhysics(AimConfig())
        cursor = physics.step((0, 0), (20, 20), 0, 1, 0, 0, 1, tracking=True)
        for i in range(40):
            cursor = physics.step((cursor[0] + 1, cursor[1]), (i + 21, 20), 0, 1,
                                  (i + 1) * .005, .005, 1, tracking=True)
        self.assertAlmostEqual(physics.offset[0], 0)
        self.assertGreater(physics.offset[1], 0)
        self.assertEqual(physics.hand[0], 40)

    def test_fractional_offsets_survive_integer_cursor_feedback(self):
        physics = AssistPhysics(AimConfig(require_motion=False, on_note_hold=False))
        cursor = (100, 100)
        physics.step(cursor, (105, 100), 0, 1, 0, 0, 1)
        for index in range(200):
            cursor = physics.step(cursor, (105, 100), 0, 1, (index + 1) * 0.005, 0.005, 1)
        self.assertGreater(cursor[0], 100)
        self.assertEqual(physics.raw_position, (100, 100))
        self.assertLessEqual(cursor[0], 105)

    def test_generated_movement_is_not_mistaken_for_hand_movement(self):
        physics = AssistPhysics(AimConfig(require_motion=False, on_note_hold=False))
        cursor = physics.step((100, 100), (150, 100), 0, 1, 0, 0, 1)
        for index in range(30):
            cursor = physics.step(cursor, (150, 100), 0, 1, index * 0.01, 0.01, 1)
        self.assertEqual(physics.raw_position, (100, 100))
        physics.step((cursor[0] + 2, cursor[1]), None, 0, 1, 1, 0.01, 1)
        self.assertEqual(physics.raw_position, (102, 100))

    def test_idle_cursor_does_not_start_assisting_when_motion_required(self):
        physics = AssistPhysics(AimConfig())
        for index in range(30):
            self.assertEqual(physics.step((100, 100), (150, 100), 0, 1, index * 0.01, 0.01, 1), (100, 100))
        self.assertEqual(physics.reason, 'waiting for hand movement')

    def test_direction_gate_accepts_toward_and_rejects_away(self):
        for delta, assists in ((2, True), (-2, False)):
            with self.subTest(delta=delta):
                physics = AssistPhysics(AimConfig())
                physics.step((100, 100), (150, 100), 0, 1, 1, 0, 1)
                physics.step((100 + delta, 100), (150, 100), 0, 1, 1.01, 0.01, 1)
                self.assertEqual(physics.offset[0] > 0, assists)

    def test_on_note_hold_keeps_offset_instead_of_dragging_to_centre(self):
        physics = AssistPhysics(AimConfig())
        physics.hand, physics.last_output, physics.offset = (100, 100), (105, 100), (5, 0)
        self.assertEqual(physics.step((105, 100), (106, 100), 0, 5, 1, 0.01, 1), (105, 100))
        self.assertAlmostEqual(physics.hand[0] + physics.offset[0], 105)
        self.assertEqual(physics.reason, 'on-note hold')

    def test_tablet_absolute_packet_does_not_subtract_our_previous_offset(self):
        physics = AssistPhysics(AimConfig(tablet=True))
        physics.hand, physics.last_output, physics.offset = (100, 100), (110, 100), (10, 0)
        cursor = physics.step((100, 100), None, 0, 1, 1, 0.01, 1)
        self.assertEqual(physics.raw_position, (100, 100))
        physics.step(cursor, None, 0, 1, 1.01, 0.01, 1)
        self.assertEqual(physics.raw_position, (100, 100))
        self.assertEqual(physics.moving_until, 0)

    def test_releasing_target_does_not_pull_cursor_back_to_old_position(self):
        physics = AssistPhysics(AimConfig())
        physics.hand, physics.last_output, physics.offset = (100, 100), (110, 100), (10, 0)
        cursor = (110, 100)
        for index in range(100):
            cursor = physics.step(cursor, None, 0, 1, index * 0.01, 0.01, 1)
        self.assertEqual(cursor, (110, 100))

    def test_small_forward_error_never_rewinds_a_previous_correction(self):
        physics = AssistPhysics(AimConfig(require_motion=False, on_note_hold=False))
        physics.hand, physics.last_output, physics.offset = (100, 100), (120, 100), (20, 0)
        cursor = (120, 100)
        positions = []
        for i in range(30):
            cursor = physics.step(cursor, (125, 100), 0, 1, (i + 1) * .005, .005, 1)
            positions.append(cursor[0])
        self.assertGreaterEqual(min(positions), 120)

    def test_fractional_response_is_frame_rate_independent(self):
        def advance(dt, count):
            physics = AssistPhysics(AimConfig(require_motion=False, on_note_hold=False))
            cursor = physics.step((0, 0), (40, 0), 0, 0, 0, 0, 1)
            for index in range(count):
                cursor = physics.step(cursor, (40, 0), 0, 0, index * dt, dt, 1)
            return physics.hand[0] + physics.offset[0]
        self.assertAlmostEqual(advance(0.005, 10), advance(0.010, 5))

    def test_bad_configuration_rejected(self):
        for name, value in (('strength', 2), ('smooth_ms', 0), ('radius', float('nan')),
                            ('cone_deg', 361), ('motion_curve', float('inf'))):
            with self.subTest(name=name), self.assertRaises(ValueError):
                AimConfig(**{name: value})

    def test_offset_limit_does_not_rewind_the_other_axis(self):
        physics = AssistPhysics(AimConfig(require_motion=False, on_note_hold=False))
        physics.hand, physics.last_output, physics.offset = (100, 100), (120, 100), (20, 0)
        cursor = (120, 100)
        for i in range(40):
            cursor = physics.step(cursor, (120, 150), 0, 1, (i + 1) * .005, .005, 1)
            self.assertEqual(cursor[0], 120)
        self.assertGreater(cursor[1], 100)

    def test_manual_escape_passes_through_without_inverse_correction(self):
        physics = AssistPhysics(AimConfig())
        physics.hand, physics.last_output, physics.offset = (100, 100), (120, 100), (20, 0)
        self.assertEqual(physics.step((130, 100), None, 0, 1, 1, .01, 1), (130, 100))


if __name__ == '__main__':
    unittest.main()
