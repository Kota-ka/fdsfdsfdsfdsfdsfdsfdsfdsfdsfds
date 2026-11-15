import time
import unittest
from unittest.mock import Mock, patch

import aim
import tosu


class MotionTests(unittest.TestCase):
    def test_deadlines_compensate_small_oversleep_without_replaying_missed_ticks(self):
        with patch('aim.threading.Thread') as thread:
            self.assist.start_motion()
        run = thread.call_args.kwargs['target']
        sleeps = []
        def wait(seconds):
            sleeps.append(seconds)
            if len(sleeps) == 3:
                self.assist.motion_stop.set()
        with patch('aim.time.perf_counter',side_effect=[0,.0002,.0017,.008]), patch('aim.time.sleep',side_effect=wait):
            run()
        self.assertAlmostEqual(sleeps[0],.0008)
        self.assertAlmostEqual(sleeps[1],.0003)
        self.assertEqual(sleeps[2],0)
        self.assist.motion_thread = None
    def setUp(self):
        patcher = patch.object(aim.ctypes, 'WinDLL')
        patcher.start()
        self.addCleanup(patcher.stop)
        self.assist = aim.AimAssist()
        self.addCleanup(self.assist.stop_motion)
        self.assist._step = Mock()

    def test_motion_reuses_the_model_without_waiting_for_another_http_frame(self):
        self.assist.start_motion()
        self.assist.update({'time': 100}, 0, {})
        deadline = time.monotonic() + .1
        while self.assist._step.call_count < 5 and time.monotonic() < deadline:
            time.sleep(.002)
        self.assertGreaterEqual(self.assist._step.call_count, 5)

    def test_reset_discards_the_queued_model(self):
        self.assist.start_motion()
        self.assist.update({'time': 100}, 0, {})
        time.sleep(.01)
        self.assist.reset()
        count = self.assist._step.call_count
        time.sleep(.01)
        self.assertEqual(self.assist._step.call_count, count)

    def test_next_note_starts_without_waiting_for_another_network_frame(self):
        upcoming = dict(time=500, type=1)
        current = dict(time=10, type=1, next_aim_object=upcoming)
        self.assist.reached_notes.add(id(current))
        self.assist.clicked_notes.add(id(current))
        self.assist.start_motion()
        self.assist.update(current, 20, {})
        deadline = time.monotonic() + .1
        while not self.assist._step.called and time.monotonic() < deadline:
            time.sleep(.001)
        self.assertIs(self.assist._step.call_args.args[0], upcoming)

    def test_slider_is_not_preempted_by_the_network_independent_handoff(self):
        upcoming = dict(time=500, type=1)
        slider = dict(time=10, type=2, duration=200, next_aim_object=upcoming)
        self.assist.start_motion()
        self.assist.update(slider, 20, {})
        deadline = time.monotonic() + .1
        while not self.assist._step.called and time.monotonic() < deadline:
            time.sleep(.001)
        self.assertIs(self.assist._step.call_args.args[0], slider)

    def test_late_note_is_retained_until_arrival_and_click(self):
        note = dict(time=100, type=1)
        self.assertFalse(self.assist.can_advance(note, 101))
        self.assist.reached_notes.add(id(note))
        self.assertFalse(self.assist.can_advance(note, 101))
        self.assist.confirm_click(note)
        self.assertTrue(self.assist.can_advance(note, 101))

    def test_missing_note_expires_instead_of_blocking_the_rest_of_the_map(self):
        self.assertFalse(self.assist.can_advance(dict(time=100, type=1), 149))
        self.assertTrue(self.assist.can_advance(dict(time=100, type=1), 151))

    def test_spinner_is_retained_until_its_end(self):
        spinner = dict(time=100, duration=200, type=8)
        self.assertFalse(self.assist.can_advance(spinner, 200))
        self.assertFalse(self.assist.can_advance(spinner, 300))
        self.assertTrue(self.assist.can_advance(spinner, 301))

    def test_repeated_api_clock_does_not_restart_interpolation(self):
        self.assist.start_motion()
        for i in range(7):
            self.assist.update({'time': 100}, 0, {})
            time.sleep(.004)
        times = [call.args[1] for call in self.assist._step.call_args_list]
        self.assertGreater(times[-1], 20)
        self.assertEqual(times, sorted(times))

    def test_model_clock_advances_between_network_frames(self):
        self.assist.start_motion()
        self.assist.update({'time': 100}, 10, {'play': {'mods': {'number': 64}}})
        time.sleep(.025)
        times = [call.args[1] for call in self.assist._step.call_args_list]
        self.assertGreater(len(times), 2)
        self.assertGreater(times[-1], times[0])
        self.assertGreater(times[-1], 25)

    def test_stale_model_stops_motion(self):
        with self.assist.motion_lock:
            self.assist.latest_model = ({'time': 100}, 0, {}, time.monotonic() - 1)
        self.assist.start_motion()
        time.sleep(.01)
        self.assist._step.assert_not_called()
        self.assertIsNone(self.assist.latest_model)

    def test_relax_clicks_without_waiting_for_another_http_response(self):
        player = tosu.Playback(self.assist)
        obj = dict(time=20, type=1, duration=0)
        player.objects, player.times = [obj], [20]
        player.keys, player.input_active = ('z', 'x'), True
        self.assist.click_ready = Mock(return_value=True)
        self.assist.input_focused = Mock(return_value=True)
        with patch.object(tosu, 'keyboard') as keyboard:
            self.assist.start_motion()
            try:
                self.assist.update(obj, 0, {})
                deadline = time.monotonic() + .12
                while not keyboard.press.called and time.monotonic() < deadline:
                    time.sleep(.001)
                keyboard.press.assert_called_once_with('z')
                self.assertEqual(player.index, 1)
                self.assertIn(id(obj), self.assist.clicked_notes)
            finally:
                self.assist.stop_motion()

    def test_stale_feed_releases_keys_held_by_the_input_worker(self):
        player = tosu.Playback(self.assist)
        player.held['z'] = 1000
        player.input_active = True
        with patch.object(tosu, 'keyboard') as keyboard:
            with self.assist.motion_lock:
                self.assist.latest_model = ({'time': 100}, 0, {}, time.monotonic() - 1)
            self.assist.start_motion()
            try:
                deadline = time.monotonic() + .1
                while player.held and time.monotonic() < deadline:
                    time.sleep(.001)
                keyboard.release.assert_called_once_with('z')
                self.assertFalse(player.input_active)
            finally:
                self.assist.stop_motion()

    def test_focus_loss_cancels_worker_inputs_before_another_api_frame(self):
        player = tosu.Playback(self.assist)
        player.held['z'] = 1000
        player.input_active, player.keys = True, ('z', 'x')
        self.assist.input_focused = Mock(return_value=False)
        with patch.object(tosu, 'keyboard') as keyboard:
            player.input_tick(100)
            keyboard.release.assert_called_once_with('z')
            keyboard.press.assert_not_called()
            self.assertFalse(player.input_active)

    def test_clock_anchor_includes_time_spent_receiving_the_response(self):
        self.assist.motion_thread = Mock()
        sampled_at = time.monotonic() - .01
        self.assist.update({'time': 100}, 0, {}, sampled_at=sampled_at)
        self.assertEqual(self.assist.latest_model[3], sampled_at)
        self.assist.motion_thread = None

    def test_timing_feedback_is_applied_gradually(self):
        self.assist.timing_target_ms = 15
        self.assist.start_motion()
        self.assist.update({'time': 1000}, 0, {})
        time.sleep(.03)
        self.assertGreater(self.assist.timing_correction_ms, 0)
        self.assertLess(self.assist.timing_correction_ms, 15)


if __name__ == '__main__':
    unittest.main()
