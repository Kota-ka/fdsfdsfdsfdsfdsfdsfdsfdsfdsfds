import unittest

from relax_timing import TimingFeedback


class TimingFeedbackTests(unittest.TestCase):
    def test_eight_fresh_hits_learn_late_bias_and_converge(self):
        timing = TimingFeedback()
        timing.observe([15] * 7, 0)
        self.assertEqual(timing.target_ms, 0)
        timing.observe([15] * 8, 0)
        self.assertEqual(timing.target_ms, 15)
        timing.observe([15] * 8 + [0] * 8, 15)
        self.assertEqual(timing.target_ms, 15)

    def test_early_hits_adjust_the_clock_in_the_other_direction(self):
        timing = TimingFeedback()
        timing.observe([-12] * 8, 4)
        self.assertEqual(timing.target_ms, -8)

    def test_median_rejects_outliers_and_limits_correction(self):
        timing = TimingFeedback()
        timing.observe([10] * 6 + [-90, 90], 0)
        self.assertEqual(timing.target_ms, 10)
        timing.observe([10] * 6 + [-90, 90] + [80] * 8, 10)
        self.assertEqual(timing.target_ms, 12)

    def test_initial_correction_is_bounded_and_later_changes_are_small(self):
        timing = TimingFeedback()
        timing.observe([90] * 8, 0)
        self.assertEqual(timing.target_ms, 30)
        timing.observe([90] * 8 + [-90] * 8, 30)
        self.assertEqual(timing.target_ms, 28)

    def test_late_position_errors_are_not_used_to_calibrate_the_clock(self):
        timing = TimingFeedback()
        timing.observe([94, 91, 91] + [4] * 8, 0, maximum_error_ms=25)
        self.assertEqual(timing.target_ms, 4)

    def test_duplicate_frames_do_not_apply_feedback_twice(self):
        timing = TimingFeedback()
        timing.observe([10] * 8, 0)
        timing.observe([10] * 8, 10)
        self.assertEqual(timing.target_ms, 10)

    def test_pause_discards_old_hits_but_preserves_learned_offset(self):
        timing = TimingFeedback()
        timing.observe([10] * 8, 0)
        timing.reset_history([10] * 8)
        timing.observe([10] * 8, 10)
        self.assertEqual(timing.target_ms, 10)

    def test_invalid_feedback_is_ignored(self):
        timing = TimingFeedback()
        timing.observe([float('nan'), float('inf'), True, '15', 1000], 0)
        self.assertEqual(timing.target_ms, 0)
        self.assertFalse(timing.pending)
