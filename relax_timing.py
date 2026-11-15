"""Bounded timing feedback using osu!'s own signed hit errors in game milliseconds."""

import math
import statistics


class TimingFeedback:
    def __init__(self):
        self.target_ms = 0.0
        self.median_ms = None
        self.seen = 0
        self.pending = []
        self.initialized = False

    def reset_history(self, errors):
        self.seen = len(errors) if isinstance(errors, list) else 0
        self.pending.clear()
        self.median_ms = None

    def observe(self, errors, applied_ms, maximum_error_ms=100):
        if not isinstance(errors, list):
            return
        if len(errors) < self.seen:
            self.reset_history([])
        fresh = errors[max(self.seen, len(errors) - 64):]
        self.seen = len(errors)
        self.pending.extend(float(value) for value in fresh
                            if isinstance(value, (int, float)) and not isinstance(value, bool)
                            and math.isfinite(value) and abs(value) <= maximum_error_ms)
        while len(self.pending) >= 8:
            self.median_ms = statistics.median(self.pending[:8])
            del self.pending[:8]
            desired = max(-30, min(30, applied_ms + self.median_ms))
            if not self.initialized:
                self.target_ms, self.initialized = desired, True
            elif abs(self.median_ms) >= 1:
                self.target_ms += max(-2, min(2, (desired-self.target_ms)*.25))
