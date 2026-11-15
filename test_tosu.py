import copy
import os
import unittest
from unittest.mock import Mock, patch

import tosu


def frame(live=0):
    return {
        'game': {'focused': True, 'paused': False},
        'state': {'number': 2},
        'play': {'failed': False, 'mode': {'number': 0}},
        'beatmap': {'time': {'live': live}},
        'folders': {'songs': os.path.abspath('Songs')},
        'directPath': {'beatmapFile': os.path.join('map', 'test.osu')},
        'settings': {'keybinds': {'osu': {'k1': 'Z', 'k2': 'X'}}},
    }


class TosuTests(unittest.TestCase):
    def test_empty_lazer_keys_never_reach_keyboard_input(self):
        data = frame()
        data['client'] = 'lazer'
        data['settings']['keybinds']['osu'] = {'k1':'','k2':''}
        with self.assertRaisesRegex(ValueError,'Missing'):
            self.player.update(data)
        self.keyboard.press.assert_not_called()
        self.assertFalse(self.player.input_active)
        self.assertEqual(tosu.snapshot(data,('z','x'))[3],('z','x'))

    def test_lazer_loading_waits_for_clock_progress_and_resets_on_retry(self):
        assist = self.make_aim()
        self.player.aim = assist
        data = frame(0)
        data['client'] = 'lazer'
        for now in (0,.05,.1,.2):
            self.player.update(data,sampled_at=now)
        assist.update.assert_not_called()
        self.keyboard.press.assert_not_called()
        for now,live in ((.25,20),(.3,70),(.4,170)):
            data['beatmap']['time']['live'] = live
            self.player.update(data,sampled_at=now)
        assist.update.assert_called()
        assist.update.reset_mock()
        data['beatmap']['time']['live'] = 0
        self.player.update(data,sampled_at=.45)
        assist.update.assert_not_called()

    def test_lazer_hash_file_is_accepted_without_extension(self):
        data = frame()
        data['client'] = 'lazer'
        hashed = 'a'*64
        data['directPath']['beatmapFile'] = os.path.join('a','aa',hashed)
        self.assertTrue(tosu.snapshot(data)[1].endswith(hashed))
        data['client'] = 'stable'
        with self.assertRaises(ValueError):
            tosu.snapshot(data)

    def make_aim(self):
        assist = Mock()
        assist.motion_thread = None
        assist.clicked_notes = set()
        assist.late_window_ms = 50
        assist.can_advance.side_effect = lambda obj, live: live > obj['time'] + (obj['duration'] if obj['type'] & 10 else 0)
        return assist

    def setUp(self):
        self.keyboard = patch.object(tosu, 'keyboard').start()
        engine = Mock(approach_rate=5, stack_leniency=.7, format_version=14, hit_objects=[
            {'time': 100, 'duration': 100, 'is_slider': True, 'type': 2, 'x': 256, 'y': 192},
            {'time': 300, 'duration': 0, 'is_slider': False, 'type': 1, 'x': 300, 'y': 200},
        ])
        self.parser = patch.object(tosu, 'OsuRelaxCheatsEngine', return_value=engine).start()
        self.addCleanup(patch.stopall)
        self.player = tosu.Playback()

    def test_slider_release_and_retry(self):
        for live in (0, 100, 200):
            self.player.update(frame(live))
        self.keyboard.press.assert_called_once_with('z')
        self.keyboard.release.assert_called_once_with('z')
        self.player.update(frame(0))
        self.player.update(frame(100))
        self.assertEqual(self.keyboard.press.call_count, 2)

    def test_dense_stream_keys_are_released_between_hits(self):
        self.player.objects = [dict(time=t, duration=0, type=1) for t in (0, 5, 10, 15)]
        self.player.times = [0, 5, 10, 15]
        self.player._step_inputs(0, ('z','x'))
        self.assertAlmostEqual(self.player.held['z'], 2.25)
        self.player._step_inputs(3, ('z','x'))
        self.assertFalse(self.player.held)
        self.player._step_inputs(5, ('z','x'))
        self.player._step_inputs(8, ('z','x'))
        self.assertFalse(self.player.held)
        self.player._step_inputs(10, ('z','x'))
        self.assertEqual([call.args[0] for call in self.keyboard.press.call_args_list], ['z','x','z'])

    def test_input_diagnostics_convert_lateness_to_real_milliseconds(self):
        self.player.aim = self.make_aim()
        self.player.aim.debug = True
        self.player.current_rate = 2
        self.player.objects = [dict(time=100,duration=0,type=1)]
        self.player.times = [100]
        with patch('tosu.time.monotonic',return_value=1), patch('builtins.print') as output:
            self.player._step_inputs(110,('z','x'))
        self.assertIn('max_late=5.00ms',output.call_args.args[0])
        self.player._step_inputs(156,('z','x'))
        self.assertEqual(self.player.input_debug['release_late_ms'],3)

    def test_circle_press_duration_uses_real_time_at_different_rates(self):
        for rate in (.5,1,1.2,1.75,2):
            with self.subTest(rate=rate):
                player = tosu.Playback()
                player.current_rate = rate
                player.objects = [dict(time=100,duration=0,type=1)]
                player.times = [100]
                player._step_inputs(100,('z','x'))
                self.assertAlmostEqual((player.held['z']-100)/rate,20)
                player._step_inputs(100+20*rate,('z','x'))
                self.assertFalse(player.held)

    def test_late_notes_are_not_pressed_in_a_single_tick_burst(self):
        self.player.objects = [dict(time=t,duration=0,type=1) for t in (100,101,102)]
        self.player.times = [100,101,102]
        self.player._step_inputs(110,('z','x'))
        self.assertEqual(self.keyboard.press.call_count,1)
        self.player._step_inputs(110,('z','x'))
        self.assertEqual(self.keyboard.press.call_count,1)
        self.player._step_inputs(111,('z','x'))
        self.assertEqual(self.keyboard.press.call_count,2)

    def test_stream_notes_do_not_release_an_active_slider_key(self):
        self.player.objects = [dict(time=0,duration=100,type=2),dict(time=20,duration=0,type=1),dict(time=40,duration=0,type=1)]
        self.player.times = [0,20,40]
        for live in (0,20,30,40): self.player._step_inputs(live,('z','x'))
        self.assertEqual(self.player.held['z'], 100)
        self.assertNotIn('z',[call.args[0] for call in self.keyboard.release.call_args_list])

    def test_click_waits_for_arrival_without_consuming_the_note(self):
        assist = self.make_aim()
        assist.click_ready.return_value = False
        self.player.aim = assist
        self.player.update(frame(0))
        self.player.update(frame(100))
        self.keyboard.press.assert_not_called()
        self.assertEqual(self.player.index, 0)
        assist.click_ready.return_value = True
        self.player.update(frame(110))
        self.keyboard.press.assert_called_once_with('z')
        assist.confirm_click.assert_called_once_with(self.player.objects[0])

    def test_real_game_feedback_reaches_the_aim_clock(self):
        assist = self.make_aim()
        assist.timing_correction_ms = 0
        self.player.aim = assist
        self.player.update(frame(0))
        assist.clicked_notes.update(range(8))
        data = frame(100)
        data['play']['hitErrorArray'] = [15] * 8
        self.player.update(data)
        self.assertEqual(assist.timing_target_ms, 15)
        self.assertEqual(assist.hit_error_median_ms, 15)

    def test_late_click_window_comes_from_map_and_mod_rate(self):
        assist = self.make_aim()
        assist.click_ready.return_value = False
        self.player.aim = assist
        data = frame(0)
        data['beatmap']['stats'] = {'hitWindow': {'meh': 66, 'great': 13}}
        data['play']['mods'] = {'number': 64, 'rate': 1.5}
        self.player.update(data)
        self.assertEqual(assist.late_window_ms, 99)
        data['beatmap']['time']['live'] = 175
        self.player.update(data)
        self.assertEqual(self.player.index, 0)

    def test_stacking_recomputes_when_difficulty_mods_change(self):
        engine = self.parser.return_value
        engine.approach_rate, engine.stack_leniency = 9.7, .6
        engine.hit_objects = [dict(time=100, duration=0, type=1, x=256, y=192),
                              dict(time=600, duration=0, type=1, x=256, y=192)]
        self.player.update(frame(0))
        self.assertEqual([o['stack_height'] for o in self.player.objects], [0, 0])
        data = frame(10)
        data['play']['mods'] = {'number': 2}
        self.player.update(data)
        self.assertEqual([o['stack_height'] for o in self.player.objects], [1, 0])

    def test_unreached_note_expires_without_clicking_another_note_early(self):
        assist = self.make_aim()
        assist.click_ready.return_value = False
        self.player.aim = assist
        self.player.update(frame(0))
        self.player.update(frame(100))
        self.player.update(frame(151))
        self.assertEqual(self.player.index, 1)
        self.keyboard.press.assert_not_called()

    def spinner_map(self):
        self.parser.return_value.hit_objects = [
            dict(time=100, duration=200, type=8, x=256, y=192),
            dict(time=400, duration=0, type=1, x=300, y=200)]

    def test_spinner_holds_key_for_full_duration(self):
        self.spinner_map()
        for live in (0, 100, 200):
            self.player.update(frame(live))
        self.keyboard.press.assert_called_once_with('z')
        self.keyboard.release.assert_not_called()
        self.assertEqual(self.player.held, {'z': 300})
        self.player.update(frame(300))
        self.keyboard.release.assert_called_once_with('z')

    def test_resume_inside_spinner_reacquires_key_and_retains_aim_target(self):
        self.spinner_map()
        assist = self.make_aim()
        self.player.aim = assist
        self.player.update(frame(0))
        self.player.update(frame(100))
        paused = frame(150)
        paused['game']['paused'] = True
        self.player.update(paused)
        self.assertFalse(self.player.held)
        self.player.update(frame(200))
        self.assertIs(assist.update.call_args.args[0], self.player.objects[0])
        self.assertEqual(self.player.held, {'z': 300})
        self.assertEqual(self.keyboard.press.call_count, 2)
        self.player.update(frame(301))
        self.assertIs(assist.update.call_args.args[0], self.player.objects[1])

    def test_attaching_mid_spinner_holds_remaining_duration(self):
        self.spinner_map()
        self.player.update(frame(220))
        self.assertEqual(self.player.held, {'z': 300})
        self.player.update(frame(301))
        self.assertFalse(self.player.held)

    def test_inactive_frames_release_without_pressing(self):
        for section, key, value in (
            ('game', 'focused', False), ('game', 'paused', True),
            ('play', 'failed', True), ('state', 'number', 0),
        ):
            with self.subTest(key=key):
                self.player.update(frame(0))
                self.player.update(frame(100))
                data = frame(110)
                data[section][key] = value
                before = self.keyboard.press.call_count
                self.player.update(data)
                self.assertFalse(self.player.held)
                self.assertEqual(self.keyboard.press.call_count, before)

    def test_attaching_mid_map_skips_old_notes(self):
        self.player.update(frame(250))
        self.keyboard.press.assert_not_called()
        self.player.update(frame(300))
        self.keyboard.press.assert_called_once_with('z')

    def test_map_change_releases_held_key(self):
        self.player.update(frame(0))
        self.player.update(frame(100))
        data = frame(0)
        data['directPath']['beatmapFile'] = os.path.join('other', 'map.osu')
        self.player.update(data)
        self.assertFalse(self.player.held)
        self.assertEqual(self.parser.call_count, 2)

    def test_overdue_frames_do_not_burst_old_notes(self):
        self.player.update(frame(0))
        self.player.update(frame(1000))
        self.keyboard.press.assert_not_called()

    def test_malformed_time_and_path_rejected(self):
        for value in (None, True, float('nan'), float('inf'), '100'):
            data = frame()
            data['beatmap']['time']['live'] = value
            with self.assertRaises(ValueError):
                tosu.snapshot(data)
        data = frame()
        data['directPath']['beatmapFile'] = os.path.join('..', 'outside.osu')
        with self.assertRaises(ValueError):
            tosu.snapshot(data)

    def test_missing_state_is_not_active(self):
        data = copy.deepcopy(frame())
        del data['game']['paused']
        with self.assertRaises(KeyError):
            tosu.snapshot(data)

    def test_connection_failure_and_exit_release_keys(self):
        self.player.held['z'] = 200
        opener = Mock()
        opener.open.side_effect = OSError('connection refused')
        with patch.object(tosu, 'Playback', return_value=self.player), \
                patch.object(tosu, 'build_opener', return_value=opener), \
                patch.object(tosu.time, 'sleep', side_effect=KeyboardInterrupt):
            tosu.main([])
        self.keyboard.release.assert_called_once_with('z')
        self.assertFalse(self.player.held)

    def test_aim_target_and_pause_reset(self):
        assist = self.make_aim()
        self.player.aim = assist
        self.player.update(frame(0))
        assist.update.assert_called_once_with(self.player.objects[0], 0, frame(0))
        assist.update.reset_mock()
        data = frame(100)
        data['game']['paused'] = True
        self.player.update(data)
        assist.update.assert_not_called()
        assist.reset.assert_called()

    def test_profile_defaults_and_explicit_overrides_reach_aim(self):
        with patch('aim.AimAssist') as assist, patch.object(tosu, 'build_opener') as opener:
            opener.return_value.open.side_effect = KeyboardInterrupt
            tosu.main(['--tosu', '--aim', '--aim-profile', 'aimbot',
                       '--aim-strength', '0.8', '--aim-require-motion', '--aim-note-hold'])
            config = assist.call_args.args[0]
            self.assertEqual(config.strength, .8)
            self.assertEqual(config.max_speed, 16000)
            self.assertTrue(config.require_motion)
            self.assertTrue(config.on_note_hold)

    def test_assistance_is_the_effective_default(self):
        with patch('aim.AimAssist') as assist, patch.object(tosu, 'build_opener') as opener:
            opener.return_value.open.side_effect = KeyboardInterrupt
            tosu.main(['--tosu', '--aim'])
            config = assist.call_args.args[0]
            self.assertEqual(config.strength, .65)
            self.assertTrue(config.require_motion)
            self.assertTrue(config.on_note_hold)
            self.assertEqual(config.max_offset, 24)

    def test_active_slider_remains_target_until_its_end(self):
        assist = self.make_aim()
        self.player.aim = assist
        self.player.update(frame(0))
        self.player.update(frame(150))
        self.assertIs(assist.update.call_args.args[0], self.player.objects[0])
        self.player.update(frame(201))
        self.assertIs(assist.update.call_args.args[0], self.player.objects[1])

    def test_circles_advance_in_order_without_a_thirty_ms_hold(self):
        assist = self.make_aim()
        self.player.aim = assist
        self.player.update(frame(0))
        self.player.objects = [dict(time=100, duration=0, type=1),
                               dict(time=160, duration=0, type=1),
                               dict(time=220, duration=0, type=1)]
        self.player.times = [100, 160, 220]
        for live, index in ((99, 0), (100, 0), (101, 1), (160, 1), (161, 2)):
            self.player.update(frame(live))
            self.assertIs(assist.update.call_args.args[0], self.player.objects[index])
        self.player.update(frame(221))
        self.assertIsNone(assist.update.call_args.args[0])

    def test_disabling_auto_clicks_releases_keys_but_keeps_aim_running(self):
        self.player.update(frame(0))
        self.player.update(frame(100))
        assist = self.make_aim()
        self.player.aim = assist
        self.player.auto_clicks = False
        self.keyboard.press.reset_mock()
        self.player.update(frame(110))
        self.assertFalse(self.player.held)
        self.keyboard.press.assert_not_called()
        assist.update.assert_called_once()
        self.player.update(frame(300))
        self.player.auto_clicks = True
        self.player.update(frame(301))
        self.keyboard.press.assert_not_called()


if __name__ == '__main__':
    unittest.main()
