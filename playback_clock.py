"""Shared conversion between beatmap time and wall-clock time."""
import math


def playback_rate(frame):
    mods = frame.get('play', {}).get('mods', {})
    if 'rate' in mods:
        rate = mods['rate']
        if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not math.isfinite(rate) or not 0 < rate <= 10:
            raise ValueError('Invalid playback rate')
        return rate
    number = mods.get('number', 0)
    return 1.5 if number & (64 | 512) else .75 if number & 256 else 1
