"""Optional local tosu v2 integration; run with python osu.py --tosu."""

import bisect
import argparse
import json
import math
import os
import re
import time
import threading
from urllib.request import build_opener, ProxyHandler

import keyboard

from osu import OsuRelaxCheatsEngine
from relax_timing import TimingFeedback
from stacking import stack_heights
from playback_clock import playback_rate


def snapshot(data, key_overrides=None, require_keys=True):
    """Reject incomplete API frames rather than assuming the game is active."""
    game = data['game']
    play = data['play']
    active = (data['state']['number'] == 2
              and game['focused'] is True and game['paused'] is False
              and play['failed'] is False and play['mode']['number'] == 0)
    live = data['beatmap']['time']['live']
    if isinstance(live, bool) or not isinstance(live, (int, float)) or not math.isfinite(live):
        raise ValueError('Invalid game time')
    songs = os.path.realpath(data['folders']['songs'])
    relative = data['directPath']['beatmapFile']
    path = os.path.realpath(os.path.join(songs, relative))
    lazer_hash = data.get('client') == 'lazer' and re.fullmatch(r'[0-9a-fA-F]{64}', os.path.basename(path))
    if (not relative or os.path.commonpath([songs, path]) != songs
            or not (path.lower().endswith('.osu') or lazer_hash)):
        raise ValueError('Beatmap must be inside the Songs folder')
    keys = data['settings']['keybinds']['osu']
    overrides = key_overrides or (None,None)
    names = tuple((overrides[i] or keys.get(key) or '').strip().lower() for i,key in enumerate(('k1','k2')))
    if active and require_keys and (not all(names) or names[0] == names[1]):
        raise ValueError('Missing or duplicate osu! keys: set key1/key2 in the overlay (tosu reports empty lazer keys)')
    return active, path, live, names


class Playback:
    def __init__(self, aim=None):
        self.aim = aim
        self.path = None
        self.objects = []
        self.times = []
        self.index = 0
        self.previous = None
        self.held = {}
        self.next_key = 0
        self.aim_index = 0
        self.auto_clicks = True
        self.engine = None
        self.stack_mode = None
        self.auto_timing = True
        self.timing = TimingFeedback()
        self.lock = aim.motion_lock if aim is not None else threading.RLock()
        self.keys = None
        self.input_active = False
        self.clock_context = None
        self.key_overrides = None
        self.lazer_gate = None
        self.current_rate = 1
        self.last_press_live = None
        self.input_debug = dict(presses=0,late_ms=0,release_late_ms=0,last_report=0)
        if aim is not None:
            aim.input_tick = self.input_tick
            aim.input_release = self.release

    def release(self):
        with self.lock:
            self._release()

    def _release(self):
        self.input_active = False
        self.last_press_live = None
        if self.aim is not None:
            self.aim.reset()
        for key in list(self.held):
            keyboard.release(key)
            del self.held[key]

    def update(self, frame, sampled_at=None):
        with self.lock:
            self._update(frame, sampled_at)

    def _update(self, frame, sampled_at=None):
        active, path, live, keys = snapshot(frame, self.key_overrides, self.auto_clicks)
        self.current_rate = playback_rate(frame)
        if not active:
            self.lazer_gate = None
            if self.aim is not None:
                self.aim.status('paused, unfocused or not playing')
            self.release()
            self.previous = None
            return
        if frame.get('client') == 'lazer':
            now = sampled_at if sampled_at is not None else time.monotonic()
            gate = self.lazer_gate
            if gate is None or gate['path'] != path or live < gate['live']:
                gate = self.lazer_gate = dict(path=path,live=live,start=now,advances=0,ready=False,last_advance=now)
            if live > gate['live']:
                gate['advances'] += 1
                gate['last_advance'] = now
            if not gate['ready'] and now-gate['last_advance'] > .1:
                gate['start'],gate['advances'] = now,0
            gate['live'] = live
            gate['ready'] = gate['ready'] or (now-gate['start'] >= .15 and gate['advances'] >= 2)
            if not gate['ready']:
                self.release()
                self.previous = None
                if self.aim is not None:
                    self.aim.status('waiting for lazer gameplay clock')
                return
        if path != self.path:
            self.release()
            engine = OsuRelaxCheatsEngine(path)
            self.engine, self.stack_mode = engine, None
            self.objects = engine.hit_objects
            self.times = [obj['time'] for obj in self.objects]
            if self.times != sorted(self.times):
                raise ValueError('Hit objects must be ordered by time')
            targets = [obj for obj in self.objects if obj['type'] & 11 and 'x' in obj and 'y' in obj]
            for current, upcoming in zip(targets, targets[1:]):
                current['next_aim'] = (upcoming['x'], upcoming['y'], upcoming['time'] - current['time'])
                current['next_aim_object'] = upcoming
            self.path = path
            self.previous = None
            print(f'[+] tosu: {os.path.basename(path)}')

        stack_mode = frame.get('play', {}).get('mods', {}).get('number', 0) & 18
        if stack_mode != self.stack_mode:
            ar = self.engine.approach_rate * (1.4 if stack_mode & 16 else .5 if stack_mode & 2 else 1)
            ar = min(10, max(0, ar))
            preempt = 1800 - 120 * ar if ar < 5 else 1200 - 150 * (ar - 5)
            heights = stack_heights(self.objects, int(preempt) * self.engine.stack_leniency, self.engine.format_version)
            for obj, height in zip(self.objects, heights):
                obj['stack_height'] = height
            self.stack_mode = stack_mode

        if self.previous is None or live < self.previous:
            self.release()
            # Do not replay old notes when attaching or resuming mid-map.
            self.index = bisect.bisect_left(self.times, live)
            self.aim_index = max(0, bisect.bisect_right(self.times, live) - 1)
            self.next_key = 0
            self.timing.reset_history(frame.get('play', {}).get('hitErrorArray'))
        self.previous = live
        self.keys, self.input_active = keys, True

        if self.aim is not None:
            context = (frame.get('client'), playback_rate(frame))
            if self.clock_context is not None and context != self.clock_context:
                self.timing = TimingFeedback()
                self.timing.reset_history(frame.get('play', {}).get('hitErrorArray'))
                self.aim.timing_target_ms = self.aim.timing_correction_ms = 0
            self.clock_context = context
            self.aim.auto_clicks = self.auto_clicks
            windows = frame.get('beatmap', {}).get('stats', {}).get('hitWindow', {})
            rate = playback_rate(frame)
            late = windows.get('meh')
            if (isinstance(late, (int, float)) and not isinstance(late, bool)
                    and isinstance(rate, (int, float)) and not isinstance(rate, bool) and math.isfinite(late) and math.isfinite(rate)
                    and 0 < late * rate <= 250):
                self.aim.late_window_ms = late * rate
            self.aim.click_settle_limit_ms = 20
            great = windows.get('great')
            if isinstance(great, (int, float)) and not isinstance(great, bool) and math.isfinite(great) and great > 0:
                self.aim.click_settle_limit_ms = max(1, min(20, great*.35))
            if self.auto_timing and self.auto_clicks and len(self.aim.clicked_notes) >= 8:
                great = windows.get('great')
                error_limit = 100
                if (isinstance(great, (int, float)) and not isinstance(great, bool)
                        and isinstance(rate, (int, float)) and not isinstance(rate, bool) and math.isfinite(great) and math.isfinite(rate)
                        and great > 0 and rate > 0):
                    error_limit = max(1, min(100, great * rate * 1.25))
                self.timing.observe(frame.get('play', {}).get('hitErrorArray'), self.aim.timing_correction_ms, error_limit)
            elif not self.auto_timing or not self.auto_clicks:
                self.timing.reset_history(frame.get('play', {}).get('hitErrorArray'))
            self.aim.timing_target_ms = self.timing.target_ms if self.auto_timing and self.auto_clicks else 0
            self.aim.hit_error_median_ms = self.timing.median_ms
            while self.aim_index < len(self.objects):
                obj = self.objects[self.aim_index]
                if obj['type'] & 11 and not self.aim.can_advance(obj, live):
                    break
                self.aim_index += 1
            target = self.objects[self.aim_index] if self.aim_index < len(self.objects) else None
            if sampled_at is None:
                self.aim.update(target, live, frame)
            else:
                self.aim.update(target, live, frame, sampled_at=sampled_at)

        if self.aim is None or self.aim.motion_thread is None or not self.auto_clicks:
            self._step_inputs(live, keys)

    def input_tick(self, live):
        with self.lock:
            if self.input_active and self.keys is not None:
                if self.aim is not None and not self.aim.input_focused():
                    self.release()
                    return
                self._step_inputs(live, self.keys)

    def _step_inputs(self, live, keys):

        if not self.auto_clicks:
            for key in list(self.held):
                keyboard.release(key)
                del self.held[key]
            self.index = bisect.bisect_right(self.times, live)
            return

        for key, end in list(self.held.items()):
            if live >= end or key not in keys:
                if self.aim is not None and self.aim.debug:
                    self.input_debug['release_late_ms'] = max(self.input_debug['release_late_ms'],max(0,live-end)/self.current_rate)
                keyboard.release(key)
                del self.held[key]

        active_index = bisect.bisect_right(self.times, live) - 1
        if active_index >= 0:
            active_object = self.objects[active_index]
            if active_object['type'] & 8 and live < active_object['time'] + active_object['duration']:
                if self.index < active_index or not self.held:
                    self.index = active_index

        while self.index < len(self.objects) and self.times[self.index] <= live:
            if self.last_press_live == live:
                break
            obj = self.objects[self.index]
            # shortcut: late API frames skip overdue notes; use a faster feed if needed.
            ongoing_spinner = obj['type'] & 8 and live < obj['time'] + obj['duration']
            if live - obj['time'] > (self.aim.late_window_ms if self.aim is not None else 50) and not ongoing_spinner:
                self.index += 1
                continue
            if self.aim is not None and obj['type'] & 3 and not self.aim.click_ready(obj, live):
                break
            self.index += 1
            key_index = self.next_key
            if keys[key_index] in self.held and keys[1-key_index] not in self.held:
                key_index = 1-key_index
            key = keys[key_index]
            self.next_key = 1-key_index
            if key in self.held:
                keyboard.release(key)
            keyboard.press(key)
            self.last_press_live = live
            if self.aim is not None:
                self.aim.confirm_click(obj)
            hold = 20 * self.current_rate
            if obj['type'] & 1 and self.index < len(self.times):
                hold = min(hold, max(2*self.current_rate, (self.times[self.index]-obj['time'])*.45))
            self.held[key] = max(live + hold, obj['time'] + obj['duration'])
            if self.aim is not None and self.aim.debug:
                stats = self.input_debug
                stats['presses'] += 1
                stats['late_ms'] = max(stats['late_ms'],max(0,live-obj['time'])/self.current_rate)
                now = time.monotonic()
                if now-stats['last_report'] >= 1:
                    print(f"[*] Relax cadence: presses={stats['presses']}; max_late={stats['late_ms']:.2f}ms; "
                          f"max_release_late={stats['release_late_ms']:.2f}ms; rate={self.current_rate:g}x")
                    self.input_debug = dict(presses=0,late_ms=0,release_late_ms=0,last_report=now)
            break


def main(argv=None):
    parser = argparse.ArgumentParser(description='osu! Relax via local tosu API')
    parser.add_argument('--tosu', action='store_true', help='Use the local tosu v2 API')
    parser.add_argument('--aim', action='store_true', help='Enable motion-driven aim with automatic gameplay projection')
    parser.add_argument('--overlay', action='store_true', help='Install/connect the native tosu configuration and FOV overlay')
    parser.add_argument('--tosu-dir', help='tosu folder, if automatic detection is unavailable')
    parser.add_argument('--aim-profile', choices=('assist', 'strong', 'aimbot'), default='assist',
                        help='Assist preset (default: assist); aimbot is a legacy alias for strong assistance')
    parser.add_argument('--aim-hold-key', help='Optional activation key, e.g. "right ctrl"')
    parser.add_argument('--aim-debug', action='store_true', help='Print aim blocking reasons at most once per second')
    parser.add_argument('--aim-tablet', action='store_true', help='Treat observed physical cursor changes as absolute tablet positions')
    parser.add_argument('--aim-idle-dance', action='store_true', default=None, help='Move in smooth loops during gameplay gaps')
    parser.add_argument('--aim-no-require-motion', dest='aim_require_motion', action='store_false', default=None, help='Allow correction without hand movement')
    parser.add_argument('--aim-require-motion', dest='aim_require_motion', action='store_true', default=None, help='Require hand movement')
    parser.add_argument('--aim-no-note-hold', dest='aim_note_hold', action='store_false', default=None, help='Allow pull toward the centre even inside the hit circle')
    parser.add_argument('--aim-note-hold', dest='aim_note_hold', action='store_true', default=None, help='Preserve offset inside the hit circle')
    from aim_physics import AimConfig, AIM_PROFILES
    fields = ('strength', 'smooth_ms', 'radius', 'timing_ms', 'cone_deg',
              'return_close_ms', 'return_far_ms', 'motion_curve', 'max_speed', 'max_offset', 'max_accel', 'hand_gain', 'curvature', 'wiggle', 'click_margin', 'damping_ratio', 'coast_ms', 'click_settle_ms', 'slider_smooth_ms')
    for name in fields:
        parser.add_argument('--aim-' + name.replace('_', '-'), type=float, default=None,
                            help=f'Override profile {name.replace("_", " ")}')
    args = parser.parse_args(argv)
    if args.overlay and not args.aim:
        parser.error('--overlay requires --aim')
    aim = None
    if args.aim:
        from aim import AimAssist
        try:
            values = dict(AIM_PROFILES[args.aim_profile])
            values.update({name: getattr(args, 'aim_' + name) for name in fields
                           if getattr(args, 'aim_' + name) is not None})
            if args.aim_require_motion is not None:
                values['require_motion'] = args.aim_require_motion
            if args.aim_idle_dance is not None:
                values['idle_dance'] = args.aim_idle_dance
            if args.aim_note_hold is not None:
                values['on_note_hold'] = args.aim_note_hold
            config = AimConfig(**values, tablet=args.aim_tablet)
        except ValueError as exc:
            parser.error(str(exc))
        aim = AimAssist(config, args.aim_hold_key, args.aim_debug)
    playback = Playback(aim)
    overlay = None
    if args.overlay:
        from tosu_overlay import TosuOverlay, find_tosu
        try:
            overlay = TosuOverlay(find_tosu(args.tosu_dir), aim, playback)
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
        print('[*] Aim settings: http://127.0.0.1:24050/AimAssist/?configure=1')
        print('[*] FOV overlay: http://127.0.0.1:24050/AimAssist/')
    if aim is not None:
        source = 'tosu saved settings' if overlay is not None else args.aim_profile
        print(f'[*] Aim settings source: {source}; effective config: {aim.physics.config}')
    # Local telemetry should never be forwarded through system HTTP proxies.
    opener = build_opener(ProxyHandler({}))
    if overlay is not None:
        try:
            overlay.register_native(opener)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            print(f'[!] FOV layer registration failed: {exc}. Add AimAssist in the tosu in-game overlay dashboard.')
    failed = False
    print('[*] tosu mode: http://127.0.0.1:24050/json/v2; Ctrl+C to exit')
    if aim is not None:
        aim.start_motion()
    try:
        while True:
            try:
                if overlay is not None:
                    overlay.poll()
                request_started = time.monotonic()
                with opener.open('http://127.0.0.1:24050/json/v2', timeout=0.1) as response:
                    frame = json.loads(response.read(2_000_000))
                sampled_at = (request_started + time.monotonic()) / 2
                if aim is not None:
                    aim.calibrate(frame)
                playback.update(frame, sampled_at=sampled_at)
                failed = False
                time.sleep(0.005)
            except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
                playback.release()
                playback.previous = None
                if not failed:
                    print(f'[!] Telemetry or input unavailable: {exc}')
                failed = True
                time.sleep(0.1)
            finally:
                if overlay is not None:
                    overlay.publish()
    except KeyboardInterrupt:
        pass
    finally:
        playback.release()
        if aim is not None:
            aim.stop_motion()
        if overlay is not None:
            overlay.close()


if __name__ == '__main__':
    main()
