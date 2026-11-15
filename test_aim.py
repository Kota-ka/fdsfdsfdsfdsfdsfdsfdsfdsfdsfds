import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import aim
from osu import OsuRelaxCheatsEngine


class AimTests(unittest.TestCase):
    def test_debug_pairs_largest_correction_with_its_own_tick(self):
        self.assist.debug = True
        with patch.object(self.assist.physics,'step',return_value=(400,200)), patch('builtins.print') as output:
            self.assist.update(self.obj,100,self.frame)
        lines = [call.args[0] for call in output.call_args_list]
        self.assertTrue(any('max_correction=100.00px; correction_tick=10.00ms' in line for line in lines))
    def setUp(self):
        self.dll = patch.object(aim.ctypes, 'WinDLL').start().return_value
        self.keyboard = patch.object(aim, 'keyboard').start()
        self.clock = patch.object(aim.time, 'monotonic', return_value=1).start()
        self.addCleanup(patch.stopall)
        self.assist = aim.AimAssist()
        self.assist.window = (10, 20, 0, 0, 1000, 800)
        self.assist.geometry = Mock(return_value=self.assist.window)
        self.assist.cursor = Mock(return_value=(300, 200))
        self.assist.bounds = (100, 100, 512, 384)
        self.assist.manual = True
        self.assist.previous = 0.99
        self.obj = {'time': 100, 'type': 1, 'x': 256, 'y': 100}
        self.frame = {'client': 'stable', 'play': {'mods': {'number': 0}}}
        self.keyboard.is_pressed.return_value = True

    def test_calibration_requires_two_valid_corners(self):
        self.assist.bounds = None
        pressed = {'f6'}
        self.keyboard.is_pressed.side_effect = lambda key: key in pressed
        self.assist.cursor.return_value = (100, 100)
        self.assist.calibrate()
        self.assertIsNone(self.assist.bounds)
        pressed.clear()
        self.assist.calibrate()
        pressed.add('f7')
        self.assist.cursor.return_value = (612, 484)
        self.assist.calibrate()
        self.assertEqual(self.assist.bounds, (100, 100, 512, 384))

    def test_invalid_calibration_stays_disabled(self):
        self.assist.bounds = None
        self.assist.corner = (100, 100)
        self.assist.cursor.return_value = (400, 400)
        self.keyboard.is_pressed.side_effect = lambda key: key == 'f7'
        self.assist.calibrate()
        self.assertIsNone(self.assist.bounds)
        self.assertFalse(self.assist.manual)

    def test_hard_rock_mirrors_y(self):
        self.frame['play']['mods']['number'] = 16
        with patch.object(self.assist.physics, 'step', return_value=(310, 210)) as smooth:
            self.assist.update(self.obj, 100, self.frame)
        smooth.assert_called_once()
        self.assertEqual(smooth.call_args.args[1], (356, 384))
        self.dll.SetCursorPos.assert_called_once_with(310, 210)

    def test_stacked_hit_position_matches_visual_offset_with_and_without_hr(self):
        self.obj['stack_height'] = 2
        for mods, expected_y in ((0, 200), (16, 384)):
            self.frame['play']['mods']['number'] = mods
            with patch.object(self.assist.physics, 'step', return_value=(300, 200)) as step:
                self.assist.update(self.obj, 100, self.frame)
            target = step.call_args.args[1]
            self.assertAlmostEqual(target[0], 356 - 7.296)
            self.assertAlmostEqual(target[1], expected_y - 7.296)
            self.assertEqual(self.assist.telemetry['note_head'], target)

    def test_arrival_click_and_handoff_are_synchronized(self):
        with patch.object(self.assist.physics, 'step', return_value=(356, 200)):
            self.assist.update(self.obj, 101, self.frame)
            self.assertFalse(self.assist.click_ready(self.obj, 101))
            self.assist.cursor.return_value = (356, 200)
            self.assist.update(self.obj, 102, self.frame)
        self.assertFalse(self.assist.can_advance(self.obj, 101))
        self.assist.cursor.return_value = (356, 200)
        self.assertTrue(self.assist.click_ready(self.obj, 101))
        self.assist.cursor.return_value = (700, 200)
        self.assertFalse(self.assist.click_ready(self.obj, 101))
        self.assist.confirm_click(self.obj)
        self.assertTrue(self.assist.can_advance(self.obj, 101))

    def test_pending_note_is_detected_even_when_aim_is_tracking_another_head(self):
        with patch.object(self.assist.physics, 'step', return_value=(300, 200)):
            self.assist.update(self.obj, 100, self.frame)
        pending = dict(time=95, duration=0, type=1, x=200, y=100)
        self.assertIsNone(self.assist.near_note)
        self.assertTrue(self.assist.click_ready(pending, 100))
        self.assist.confirm_click(pending)
        self.assertTrue(self.assist.can_advance(pending, 100))

    def test_click_margin_rejects_border_but_accepts_inside(self):
        with patch.object(self.assist.physics, 'step', return_value=(300, 200)):
            self.assist.update(self.obj, 100, self.frame)
        radius = self.assist.telemetry['hit_radius']
        self.assist.cursor.return_value = (356 + radius * .95, 200)
        self.assist.observed_cursor = self.assist.cursor.return_value
        self.assertFalse(self.assist.click_ready(self.obj, 100))
        self.assist.cursor.return_value = (356 + radius * .8, 200)
        self.assist.observed_cursor = self.assist.cursor.return_value
        self.assertTrue(self.assist.click_ready(self.obj, 100))

    def test_cursor_must_be_observed_inside_before_the_click_not_just_commanded(self):
        def move(x, y):
            self.assist.cursor.return_value = (x, y)
            return True
        self.dll.SetCursorPos.side_effect = move
        with patch.object(self.assist.physics, 'step', return_value=(356, 200)):
            self.assist.update(self.obj, 100, self.frame)
            self.assertEqual(self.assist.cursor(), (356, 200))
            self.assertFalse(self.assist.click_ready(self.obj, 100))
            self.assist.update(self.obj, 101, self.frame)
            self.assertTrue(self.assist.click_ready(self.obj, 101))

    def test_click_requires_continuous_stable_position_when_enabled(self):
        self.assist.physics.config.click_settle_ms = 3
        self.assist.cursor.return_value = (356, 200)
        with patch.object(self.assist.physics, 'step', return_value=(356, 200)):
            self.assist.update(self.obj, 100, self.frame)
            self.assertFalse(self.assist.click_ready(self.obj, 100))
            self.clock.return_value += .004
            self.assertTrue(self.assist.click_ready(self.obj, 104))
            self.assist.cursor.return_value = (700, 200)
            self.assertFalse(self.assist.click_ready(self.obj, 104))
            self.assist.cursor.return_value = (356, 200)
            self.assertFalse(self.assist.click_ready(self.obj, 104))

    def test_motion_can_leave_slightly_early_without_advancing_click_order(self):
        self.assist.physics.config.require_motion = False
        self.obj['next_aim_object'] = dict(time=200,type=1,x=300,y=100,duration=0)
        self.assist.cursor.return_value = (356,200)
        with patch.object(self.assist.physics,'step',return_value=(356,200)):
            self.assist.update(self.obj,95,self.frame)
        self.assertTrue(self.assist.motion_can_advance(self.obj,98))
        self.assertFalse(self.assist.can_advance(self.obj,98))

    def test_active_slider_projects_curve_position_and_enables_lateral_tracking(self):
        path = Mock()
        path.position.side_effect = lambda elapsed, duration, repeats: (250 + elapsed, 150 + 2 * elapsed)
        path.points = [(256, 100), (300, 250)]
        self.obj.update(type=2, duration=100, slider_path=path, slider_repeats=2)
        self.assist.reached_notes.add(id(self.obj))
        self.assist.confirm_click(self.obj)
        self.frame['play']['mods']['number'] = 16
        with patch.object(self.assist.physics, 'step', return_value=(300, 200)) as step:
            self.assist.update(self.obj, 150, self.frame)
        self.assertEqual(step.call_args.args[1], (400, 234))
        self.assertEqual(step.call_args.args[2], 0)
        self.assertTrue(step.call_args.kwargs['tracking'])
        self.assertEqual(step.call_args.kwargs['target_velocity'], (1000, -2000))

    def test_no_movement_without_activation_or_calibration(self):
        self.assist.hold_key = 'right ctrl'
        self.keyboard.is_pressed.return_value = False
        self.assist.update(self.obj, 100, self.frame)
        self.keyboard.is_pressed.return_value = True
        self.assist.bounds = None
        self.assist.update(self.obj, 100, self.frame)
        self.dll.SetCursorPos.assert_not_called()

    def test_slider_head_is_held_until_the_click_is_confirmed(self):
        path = Mock(points=[])
        self.obj.update(type=2, duration=100, slider_path=path, slider_repeats=1)
        self.assist.reached_notes.add(id(self.obj))
        with patch.object(self.assist.physics, 'step', return_value=(300, 200)) as step:
            self.assist.update(self.obj, 120, self.frame)
        self.assertFalse(step.call_args.kwargs['tracking'])
        path.position.assert_not_called()

    def test_jump_curve_is_sampled_without_replanning_on_every_frame(self):
        config = self.assist.physics.config
        config.curvature, config.require_motion = .65, False
        config.radius = 640
        self.obj['next_aim'] = (400, 200, 100)
        with patch.object(self.assist.physics, 'step', return_value=(300, 200)) as step:
            self.assist.update(self.obj, 0, self.frame)
            path = self.assist.jump_path
            self.assist.update(self.obj, 50, self.frame)
        self.assertIs(self.assist.jump_path, path)
        self.assertTrue(step.call_args.kwargs['following'])
        self.assertNotEqual(step.call_args.args[1], (356, 200))
        self.assertGreater(len(self.assist.telemetry['path']), 10)

    def test_jump_planner_respects_destination_capture_radius(self):
        config = self.assist.physics.config
        config.curvature, config.require_motion, config.radius = .65, False, 1
        self.assist.update(self.obj, 0, self.frame)
        self.assertIsNone(self.assist.jump_path)

    def test_gap_travel_yields_to_manual_motion(self):
        config = self.assist.physics.config
        config.curvature, config.require_motion, config.radius, config.coast_ms = 1, False, 740, 150
        with patch.object(self.assist.physics, 'step', return_value=(300, 200)) as step:
            self.assist.update(self.obj, -900, self.frame)
        self.assertIsNotNone(self.assist.jump_path)
        self.assertEqual(step.call_args.args[2], config.timing_ms)
        self.assertEqual(self.obj['time'], 100)
        self.assertFalse(self.assist.can_advance(self.obj, -900))
        self.assist.physics.last_output = (299,200)
        with patch.object(self.assist.physics,'step',return_value=(300,200)) as step:
            self.assist.update(self.obj,-890,self.frame)
        self.assertIsNone(self.assist.jump_path)
        self.assertIsNone(step.call_args.args[1])
        self.assertEqual(step.call_args.args[2],990)

    def test_circle_aim_point_is_stable_inside_small_hit_margin(self):
        config = self.assist.physics.config
        config.curvature, config.require_motion, config.radius = 1, False, 740
        with patch.object(self.assist.physics,'step',return_value=(300,200)):
            self.assist.update(self.obj,0,self.frame)
            endpoint = self.assist.jump_path.end
            self.assist.update(self.obj,20,self.frame)
            self.assertEqual(endpoint,self.assist.jump_path.end)
        self.assertNotEqual(endpoint,(356,200))
        self.assertLessEqual(aim.math.dist(endpoint,(356,200)),(54.4-4.48*4)*.2)

    def test_short_close_notes_do_not_get_decorative_wiggle(self):
        config = self.assist.physics.config
        config.wiggle, config.curvature, config.require_motion, config.radius = 1, 0, False, 740
        with patch.object(self.assist.physics,'step',return_value=(300,200)):
            self.assist.update(self.obj,0,self.frame)
        self.assertIsNotNone(self.assist.jump_path)
        self.assertFalse(self.assist.jump_path.wiggles)

    def test_slider_entry_has_no_wiggle_even_at_full_strength(self):
        config = self.assist.physics.config
        config.wiggle, config.curvature, config.require_motion, config.radius = 1, 1, False, 740
        path = Mock(points=[])
        path.position.return_value = (400, 100)
        self.obj.update(type=2, time=200, duration=300, slider_path=path, slider_repeats=1)
        with patch('aim.JumpPath', wraps=aim.JumpPath) as planner:
            with patch.object(self.assist.physics, 'step', return_value=(300, 200)):
                self.assist.update(self.obj, 0, self.frame)
            self.assertEqual(planner.call_args.kwargs['wiggle'], 0)
        self.assertFalse(self.assist.jump_path.wiggles)

    def test_idle_dance_runs_in_gaps_and_yields_to_the_next_note(self):
        config = self.assist.physics.config
        config.idle_dance, config.curvature, config.require_motion = True, .65, False
        with patch.object(self.assist.physics, 'step', return_value=(300, 200)) as step:
            self.assist.update(self.obj, -1000, self.frame)
            self.clock.return_value = 1.2
            self.assist.update(self.obj, -800, self.frame)
            self.assertTrue(self.assist.telemetry['idle'])
            self.assertEqual(step.call_args.args[2], 0)
            self.assist.update(self.obj, 0, self.frame)
            self.assertFalse(self.assist.telemetry['idle'])
            self.assertIsNone(self.assist.idle_path)

    def test_last_note_keeps_an_exit_tangent_for_idle_dance(self):
        config = self.assist.physics.config
        config.idle_dance, config.curvature, config.require_motion = True, .65, False
        with patch.object(self.assist.physics, 'step', return_value=(300, 200)):
            self.assist.update(self.obj, 0, self.frame)
        self.assertGreater(sum(v*v for v in self.assist.jump_path.outgoing), 0)

    def test_spinner_cancels_idle_dance_and_releases_cursor_control(self):
        config = self.assist.physics.config
        config.idle_dance, config.curvature, config.require_motion = True, .65, False
        self.obj.update(type=8, duration=200)
        self.assist.update(self.obj, 50, self.frame)
        self.assertTrue(self.assist.telemetry['idle'])
        self.assist.update(self.obj, 150, self.frame)
        self.assertIsNone(self.assist.idle_path)
        self.assertFalse(self.assist.telemetry['active'])
        self.assertEqual(self.assist.telemetry['reason'], 'spinner active')
        self.dll.SetCursorPos.assert_not_called()

    def test_spinner_and_out_of_time_targets_are_ignored(self):
        self.obj['type'] = 8
        self.obj['duration'] = 100
        self.assist.update(self.obj, 100, self.frame)
        self.obj['type'] = 1
        self.assist.update(self.obj, 1000, self.frame)
        self.dll.SetCursorPos.assert_not_called()

    def test_window_change_invalidates_calibration(self):
        self.assist.geometry.return_value = (10, 20, 10, 10, 1010, 810)
        self.assist.update(self.obj, 100, self.frame)
        self.assertIsNone(self.assist.bounds)
        self.dll.SetCursorPos.assert_not_called()

    def test_focus_loss_preserves_calibration_but_never_moves_cursor(self):
        self.assist.geometry.return_value = None
        self.assist.update(self.obj, 100, self.frame)
        self.assertIsNotNone(self.assist.bounds)
        self.dll.SetCursorPos.assert_not_called()

    def test_unknown_client_and_transform_mods_are_ignored(self):
        self.frame['client'] = 'unknown'
        self.assist.update(self.obj, 100, self.frame)
        self.frame['client'] = 'stable'
        self.frame['play']['mods']['number'] = 1 << 30
        self.assist.update(self.obj, 100, self.frame)
        self.dll.SetCursorPos.assert_not_called()

    def test_lazer_uses_automatic_projection(self):
        self.frame['client'] = 'lazer'
        self.assist.manual = False
        self.assist.bounds = None
        with patch.object(self.assist.physics,'step',return_value=(301,200)) as step:
            self.assist.update(self.obj,100,self.frame)
        step.assert_called_once()
        self.assertEqual(self.assist.bounds,aim.automatic_bounds(self.assist.window))

    def test_rate_change_replans_the_remaining_jump(self):
        config = self.assist.physics.config
        config.curvature, config.require_motion, config.radius = 1, False, 740
        with patch.object(self.assist.physics,'step',return_value=(300,200)):
            self.frame['play']['mods']['rate'] = 1.2
            self.assist.update(self.obj,0,self.frame)
            previous = self.assist.jump_path
            self.frame['play']['mods']['rate'] = .8
            self.assist.update(self.obj,20,self.frame)
        self.assertIsNot(self.assist.jump_path,previous)
        self.assertEqual(self.assist.current_rate,.8)

    def test_cursor_outside_playfield_is_not_pulled_in(self):
        self.assist.cursor.return_value = (0, 0)
        self.assist.update(self.obj, 100, self.frame)
        self.dll.SetCursorPos.assert_not_called()

    def test_changed_focus_between_read_and_move_cancels_movement(self):
        self.assist.geometry.side_effect = [self.assist.window, None]
        self.assist.cursor.return_value = (350, 200)
        with patch.object(self.assist.physics, 'step', return_value=(351, 200)):
            self.assist.update(self.obj, 100, self.frame)
        self.dll.SetCursorPos.assert_not_called()

    def test_readback_does_not_swallow_hand_movement_after_our_write(self):
        self.assist.debug = True
        self.assist.last_debug = -10
        # We send x=302. The user moves three pixels before the diagnostic read.
        self.assist.cursor.side_effect = [(300, 200), (305, 200)]
        self.assist.physics.last_output = (302, 200)
        with patch.object(self.assist.physics, 'step', return_value=(302, 200)):
            self.assist.update(self.obj, 100, self.frame)
        self.dll.SetCursorPos.assert_called_once_with(302, 200)
        self.assertEqual(self.assist.physics.last_output, (302, 200))

    def test_mouse_motion_after_write_reaches_the_next_physics_step(self):
        self.assist.debug = True
        self.assist.last_debug = -10
        self.assist.physics.hand = (300, 200)
        self.assist.physics.last_output = (300, 200)
        self.assist.physics.offset = (0, 0)
        # The correction is stationary here; test hand movement at the write/read boundary.
        def first_step(*args, **kwargs):
            self.assist.physics.last_output = (302, 200)
            return (302, 200)
        self.assist.cursor.side_effect = [(300, 200), (305, 200)]
        with patch.object(self.assist.physics, 'step', side_effect=first_step):
            self.assist.update(self.obj, 100, self.frame)
        self.assist.physics.step((305, 200), None, 0, 1, 2, .005, 1)
        self.assertEqual(self.assist.physics.raw_position, (303, 200))
        self.assertEqual(self.assist.physics.last_motion, (3, 0))

    def test_no_verification_read_on_normal_non_debug_output(self):
        with patch.object(self.assist.physics, 'step', return_value=(302, 200)):
            self.assist.update(self.obj, 100, self.frame)
        self.assist.cursor.assert_called_once()

    def test_automatic_projection_removes_calibration_requirement(self):
        self.assist.manual = False
        self.assist.bounds = None
        self.assist.window = None
        self.assist.update(self.obj, 100, self.frame)
        self.assertEqual(self.assist.bounds, aim.automatic_bounds(self.assist.window))
        self.assertIsNotNone(self.assist.physics.hand)

    def test_automatic_projection_at_1080p_and_negative_monitor_origin(self):
        self.assertEqual(aim.automatic_bounds((1, 2, 0, 0, 1920, 1080)), (384, 126, 1152, 864))
        self.assertEqual(aim.automatic_bounds((1, 2, -1920, 0, 0, 1080)), (-1536, 126, 1152, 864))

    def test_raw_input_reports_reason_without_moving(self):
        self.frame['settings'] = {'mouse': {'rawInput': True}}
        self.assist.update(self.obj, 100, self.frame)
        self.assertIn('raw input', self.assist.last_status)
        self.dll.SetCursorPos.assert_not_called()

    def test_editor_calibration_cannot_override_gameplay_projection(self):
        self.assist.manual = False
        self.keyboard.is_pressed.side_effect = lambda key: key == 'f6'
        self.assist.calibrate({'client': 'stable', 'state': {'number': 1}})
        self.assertFalse(self.assist.manual)
        self.assertIn('gameplay', self.assist.last_status)


class ParserTests(unittest.TestCase):
    def test_stacking_metadata_and_legacy_ar_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'stacking.osu'
            path.write_text('osu file format v5\n[General]\nStackLeniency:0.6\n'
                            '[Difficulty]\nOverallDifficulty:8\n'
                            '[HitObjects]\n256,192,100,1,0\n', encoding='utf-8')
            engine = OsuRelaxCheatsEngine(path)
        self.assertEqual(engine.format_version, 5)
        self.assertEqual(engine.approach_rate, 8)
        self.assertEqual(engine.stack_leniency, .6)
    def test_spinner_end_time_becomes_duration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'spinner.osu'
            path.write_text('[Difficulty]\nSliderMultiplier:1.4\n'
                            '[TimingPoints]\n0,500,4,2,0,100,1,0\n'
                            '[HitObjects]\n256,192,1000,8,0,2500\n', encoding='utf-8')
            spinner = OsuRelaxCheatsEngine(path).hit_objects[0]
        self.assertEqual(spinner['type'], 8)
        self.assertEqual(spinner['duration'], 1500)

    def test_positions_and_types_preserve_existing_timing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'test.osu'
            path.write_text('[Difficulty]\nSliderMultiplier:1.4\n'
                            '[TimingPoints]\n0,500,4,2,0,100,1,0\n'
                            '[HitObjects]\n256,100,100,1,0\n'
                            '300,200,300,2,0,B|400:200,1,140\n', encoding='utf-8')
            engine = OsuRelaxCheatsEngine(path)
        circle, slider = engine.hit_objects
        self.assertEqual((circle['x'], circle['y'], circle['type']), (256, 100, 1))
        self.assertEqual((slider['x'], slider['y'], slider['type']), (300, 200, 2))
        self.assertEqual(slider['duration'], 500)
        self.assertEqual(slider['slider_repeats'], 1)
        self.assertEqual(slider['slider_path'].position(500, 500, 1), (440, 200))


if __name__ == '__main__':
    unittest.main()
