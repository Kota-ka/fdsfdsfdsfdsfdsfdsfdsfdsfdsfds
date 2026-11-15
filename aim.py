"""Cursor assistance for osu! Stable and lazer on Windows via tosu."""

import ctypes
from ctypes import wintypes
import math
import random
import time
import threading

import keyboard
import psutil

from aim_physics import AimConfig, AssistPhysics
from aim_path import JumpPath, IdlePath, LATE_NOTE_MS
from slider_path import slider_motion
from playback_clock import playback_rate


def automatic_bounds(window):
    left, top, right, bottom = window[2:]
    width, height = right - left, bottom - top
    if width <= 0 or height <= 0:
        raise ValueError('Empty osu! client area')
    scale = min(width / 640, height / 480)
    return (left + (width - 512 * scale) / 2,
            top + (height - 384 * scale) / 2 + 8 * scale,
            512 * scale, 384 * scale)


class AimAssist:
    def __init__(self, config=None, hold_key=None, debug=False):
        self.physics = AssistPhysics(config or AimConfig())
        self.motion_lock = threading.RLock()
        self.motion_stop = threading.Event()
        self.motion_thread = None
        self.latest_model = None
        self.jump_key = self.jump_path = None
        self.jump_points = []
        self.idle_path = None
        self.reached_notes = set()
        self.clicked_notes = set()
        self.expired_notes = set()
        self.near_note = None
        self.auto_clicks = True
        self.motion_clock = None
        self.input_tick = self.input_release = None
        self.timing_target_ms = self.timing_correction_ms = 0.0
        self.hit_error_median_ms = None
        self.current_mods = 0
        self.current_rate = 1
        self.current_client = None
        self.gap_yield_until = 0
        self.observed_cursor = None
        self.late_window_ms = LATE_NOTE_MS
        self.ready_since = {}
        self.click_reasons = {}
        self.departed_notes = set()
        self.click_settle_limit_ms = 20
        self.timing_previous = None
        self.path_cache_key = None
        self.path_cache = []
        self.hold_key = hold_key
        self.debug = debug
        self.debug_override = debug
        self.last_status = None
        self.last_debug = 0.0
        self.debug_counts = {}
        self.debug_moves = 0
        self.debug_max_tick_ms = self.debug_max_step_px = 0
        self.debug_correction_tick_ms = 0
        self.enabled = True
        self.telemetry = {'active': False, 'reason': 'waiting for game'}
        self.user32 = ctypes.WinDLL('user32', use_last_error=True)
        self.user32.GetForegroundWindow.restype = wintypes.HWND
        self.user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self.user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        self.user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
        self.user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        self.user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
        self.user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        self.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        self.corner = None
        self.bounds = None
        self.window = None
        self.previous = None
        self.down = set()
        self.manual = False
        print('[*] Aim: automatic Stable/lazer playfield. F6/F7 optionally override in GAMEPLAY.')
        print(f'[*] Aim activation: {hold_key or "enabled during play"}.')

    def status(self, reason):
        self.telemetry['reason'] = reason
        if reason != self.last_status:
            print(f'[*] Aim: {reason}')
            self.last_status = reason

    def geometry(self):
        hwnd = self.user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = wintypes.DWORD()
        if not self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)):
            return None
        if self.window is None or (hwnd, pid.value) != self.window[:2]:
            try:
                if psutil.Process(pid.value).name().lower() not in ('osu!.exe', 'osu.exe', 'osulazer.exe'):
                    return None
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                return None
        rect, origin = wintypes.RECT(), wintypes.POINT()
        if not self.user32.GetClientRect(hwnd, ctypes.byref(rect)):
            raise ctypes.WinError(ctypes.get_last_error())
        if not self.user32.ClientToScreen(hwnd, ctypes.byref(origin)):
            raise ctypes.WinError(ctypes.get_last_error())
        return hwnd, pid.value, origin.x, origin.y, origin.x + rect.right, origin.y + rect.bottom

    def cursor(self):
        point = wintypes.POINT()
        if not self.user32.GetCursorPos(ctypes.byref(point)):
            raise ctypes.WinError(ctypes.get_last_error())
        return point.x, point.y

    def calibrate(self, frame=None):
        with self.motion_lock:
            self._calibrate(frame)

    def _calibrate(self, frame=None):
        pressed = {key for key in ('f6', 'f7') if keyboard.is_pressed(key)}
        edges, self.down = pressed - self.down, pressed
        if not edges:
            return
        if frame is not None and (frame.get('client') not in ('stable', 'lazer') or frame.get('state', {}).get('number') != 2):
            self.status('manual calibration is only available in gameplay, including pause')
            return
        if frame is not None:
            self.current_client = frame.get('client')
        window = self.geometry()
        if window is None:
            self.status('focus osu! before manual calibration')
            return
        point = self.cursor()
        if not (window[2] <= point[0] < window[4] and window[3] <= point[1] < window[5]):
            return
        if 'f6' in edges:
            self.manual = True
            self.corner, self.window, self.bounds = point, window, None
            self.reset()
            self.status('manual top-left recorded; F7 at bottom-right in gameplay')
        elif 'f7' in edges and self.corner is not None:
            if window != self.window:
                self.corner = None
                raise ValueError('Window changed during calibration; start again with F6')
            x, y = self.corner
            width, height = point[0] - x, point[1] - y
            if width < 64 or height < 48 or abs(width / height - 4 / 3) > 0.04:
                self.manual = False
                self.bounds = None
                self.status('invalid manual rectangle; restored automatic gameplay projection')
                return
            self.bounds = x, y, width, height
            self.reset()
            print(f'[+] Aim calibrated: {self.bounds}')

    def reset(self):
        with self.motion_lock:
            self.latest_model = None
            self.previous = None
            self.physics.reset()
            self.jump_key = self.jump_path = None
            self.jump_points = []
            self.idle_path = None
            self.reached_notes.clear()
            self.clicked_notes.clear()
            self.expired_notes.clear()
            self.near_note = None
            self.observed_cursor = None
            self.ready_since.clear()
            self.click_reasons.clear()
            self.departed_notes.clear()
            self.motion_clock = None
            self.timing_previous = None
            self.telemetry['active'] = False

    def can_advance(self, obj, live):
        with self.motion_lock:
            if obj['type'] & 10:
                return live > obj['time'] + obj['duration']
            upcoming = obj.get('next_aim_object')
            if upcoming is not None and upcoming['type'] & 8 and live >= upcoming['time']:
                return True
            if live <= obj['time']:
                return False
            if not self.enabled or (self.hold_key and not keyboard.is_pressed(self.hold_key)):
                return True
            if live > obj['time'] + self.late_window_ms:
                if (id(obj) not in self.expired_notes and id(obj) not in self.clicked_notes
                        and not (not self.auto_clicks and id(obj) in self.reached_notes)):
                    self.expired_notes.add(id(obj))
                    if self.debug:
                        print(f'[!] Aim: note at {obj["time"]} ms expired after {self.late_window_ms:.1f} ms: '
                              f'{self.click_reasons.get(id(obj), "no position sample")}')
                return True
            return id(obj) in self.reached_notes and (not self.auto_clicks or id(obj) in self.clicked_notes)

    def click_ready(self, obj, live):
        with self.motion_lock:
            if not self.enabled or (self.hold_key and not keyboard.is_pressed(self.hold_key)):
                return True
            if self.telemetry.get('active') is not True or self.bounds is None or self.geometry() != self.window:
                return False
            # Test the pending click's head, not the independently advancing aim target.
            x, y, width, height = self.bounds
            scale = min(width / 512, height / 384)
            radius = self.telemetry['hit_radius']
            offset = -.1 * radius / scale * obj.get('stack_height', 0)
            head = (x + (obj['x'] + offset) * width / 512,
                    y + ((384 - obj['y'] if self.current_mods & 16 else obj['y']) + offset) * height / 384)
            safe_radius = radius * self.physics.config.click_margin
            inside = (self.observed_cursor is not None and math.dist(self.observed_cursor, head) <= safe_radius
                      and math.dist(self.cursor(), head) <= safe_radius)
            if not inside:
                self.ready_since.pop(id(obj), None)
                self.click_reasons[id(obj)] = 'cursor outside click radius'
                return False
            since = self.ready_since.setdefault(id(obj), time.monotonic())
            settle = min(self.physics.config.click_settle_ms, self.click_settle_limit_ms)
            ready = (time.monotonic()-since)*1000 >= settle
            self.click_reasons[id(obj)] = 'ready' if ready else 'waiting for stable position'
            return ready

    def motion_can_advance(self, obj, live):
        if id(obj) in self.departed_notes or self.can_advance(obj, live):
            return True
        if (not obj['type'] & 1 or obj.get('next_aim_object') is None
                or obj['next_aim_object']['type'] & 8 or not self.auto_clicks
                or self.physics.config.require_motion or self.observed_cursor is None
                or id(obj) not in self.reached_notes or self.telemetry.get('active') is not True):
            return False
        head = self.telemetry.get('note_head')
        safe_radius = self.telemetry['hit_radius'] * self.physics.config.click_margin
        if head is None or math.dist(self.observed_cursor, head) > safe_radius*.25:
            return False
        since = self.ready_since.get(id(obj))
        settle = min(self.physics.config.click_settle_ms, self.click_settle_limit_ms)
        if since is None or (time.monotonic()-since)*1000 < settle:
            return False
        speed = math.hypot(*self.physics.velocity)
        scale = min(self.bounds[2]/512, self.bounds[3]/384)
        acceleration = self.physics.config.max_accel*scale
        budget = safe_radius*.25
        lead = min(.003, 2*budget/(speed+math.sqrt(speed*speed+2*acceleration*budget)))
        rate = self.current_rate
        if live < obj['time']-lead*1000*rate:
            return False
        self.departed_notes.add(id(obj))
        return True

    def confirm_click(self, obj):
        with self.motion_lock:
            self.clicked_notes.add(id(obj))
            self.reached_notes.add(id(obj))
            self.ready_since.pop(id(obj), None)

    def input_focused(self):
        if self.window is None:
            self.window = self.geometry()
        return self.window is not None and self.user32.GetForegroundWindow() == self.window[0]

    def start_motion(self):
        if self.motion_thread is not None and self.motion_thread.is_alive():
            return
        self.motion_stop.clear()
        def run():
            next_tick = time.perf_counter()
            while not self.motion_stop.is_set():
                try:
                    with self.motion_lock:
                        model = self.latest_model
                        if model is not None:
                            obj, live, frame, received = model
                            age = max(0, time.monotonic() - received)
                            if age > .15:
                                self.status('game telemetry stale')
                                if self.input_release is not None:
                                    self.input_release()
                                else:
                                    self.reset()
                            else:
                                mods = frame.get('play', {}).get('mods', {}).get('number', 0)
                                rate = playback_rate(frame)
                                now = time.monotonic()
                                elapsed = max(0, min(.05, now - self.timing_previous)) if self.timing_previous is not None else 0
                                self.timing_previous = now
                                self.timing_correction_ms += (self.timing_target_ms - self.timing_correction_ms) * -math.expm1(-elapsed / .25)
                                clock = max(live + age * 1000 * rate + self.timing_correction_ms,
                                            self.motion_clock if self.motion_clock is not None else live)
                                self.motion_clock = clock
                                while obj is not None and obj.get('next_aim_object') is not None:
                                    if not self.motion_can_advance(obj, clock):
                                        break
                                    obj = obj['next_aim_object']
                                if obj is not None and obj.get('type', 0) & 11 and self.can_advance(obj, clock):
                                    obj = None
                                self._step(obj, clock, frame)
                                if self.input_tick is not None:
                                    self.input_tick(clock)
                except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
                    with self.motion_lock:
                        self.status(f'motion unavailable: {exc}')
                        if self.input_release is not None:
                            self.input_release()
                        else:
                            self.reset()
                next_tick += .001
                now = time.perf_counter()
                if now-next_tick >= .001:
                    next_tick = now
                time.sleep(max(0,next_tick-now))
        self.motion_thread = threading.Thread(target=run, daemon=True)
        self.motion_thread.start()

    def stop_motion(self):
        self.motion_stop.set()
        if self.motion_thread is not None:
            self.motion_thread.join(timeout=1)
            self.motion_thread = None
        if self.input_release is not None:
            self.input_release()
        self.reset()

    def update(self, obj, live, frame, sampled_at=None):
        with self.motion_lock:
            if self.motion_thread is not None:
                received = self.latest_model[3] if self.latest_model is not None and self.latest_model[1] == live else (sampled_at if sampled_at is not None else time.monotonic())
                self.latest_model = (obj, live, frame, received)
            else:
                self._step(obj, live, frame)

    def _step(self, obj, live, frame):
        if not self.enabled:
            self.status('assist disabled')
            self.reset()
            return
        now = time.monotonic()
        previous, self.previous = self.previous, now
        if frame.get('client') not in ('stable', 'lazer'):
            self.status('unsupported client')
            self.reset()
            return
        if obj is not None and obj['type'] & 8 and obj['time'] <= live <= obj['time'] + obj['duration']:
            self.physics.reset()
            self.previous = None
            self.jump_key = self.jump_path = None
            self.jump_points = []
            self.idle_path = None
            self.near_note = None
            reason = 'spinner active' if live >= obj['time'] else 'waiting for spinner'
            self.telemetry = dict(active=False, reason=reason, spinner=live >= obj['time'],
                                  spinner_end=obj['time'] + obj['duration'], live_ms=live,
                                  timing_correction_ms=self.timing_correction_ms, hit_error_median_ms=self.hit_error_median_ms)
            self.status(reason)
            return
        if self.hold_key and not keyboard.is_pressed(self.hold_key):
            self.status(f'hold {self.hold_key} to activate')
            self.reset()
            return
        if frame.get('settings', {}).get('mouse', {}).get('rawInput') is True:
            self.status('raw input is enabled; disable it to use Windows cursor correction')
            self.reset()
            return
        mods = frame['play']['mods']['number']
        if not isinstance(mods, int) or isinstance(mods, bool) or mods < 0:
            raise ValueError('Invalid mods')
        self.current_mods = mods
        self.current_rate = playback_rate(frame)
        if frame.get('client') == 'lazer':
            supported = {'NM','NF','EZ','TD','HD','HR','SD','DT','RX','HT','NC','FL','SO','PF','DC','CL'}
            if any(mod.get('acronym') not in supported for mod in frame['play']['mods'].get('array', [])):
                self.status('unsupported lazer mod; use standard osu! mods')
                self.reset()
                return
        # shortcut: Random/Mirror coordinate transforms need separate mapping.
        if mods & ((1 << 21) | (1 << 30)):
            self.status('unsupported coordinate-transform mod')
            self.reset()
            return
        client = frame.get('client')
        if self.current_client is not None and client != self.current_client:
            self.manual, self.bounds = False, None
            self.reset()
        self.current_client = client
        window = self.geometry()
        if window is None:
            self.status('waiting for osu! focus')
            self.reset()
            return
        if window != self.window:
            if self.manual:
                self.bounds = None
                self.status('window changed; redo manual calibration with F6/F7')
                self.reset()
                return
            self.bounds = automatic_bounds(window)
            self.window = window
            self.reset()
            self.status(f'automatic gameplay area {tuple(round(v) for v in self.bounds)}')
        elif self.bounds is None and not self.manual:
            self.bounds = automatic_bounds(window)
        if self.bounds is None:
            self.status('finish manual calibration with F7')
            return
        x, y, width, height = self.bounds
        scale = min(width / 512, height / 384)
        cs = frame.get('beatmap', {}).get('stats', {}).get('cs', {}).get('converted', 4)
        if not isinstance(cs, (int, float)) or not math.isfinite(cs):
            raise ValueError('Invalid circle size')
        hit_radius = max(2, 54.4 - 4.48 * cs) * scale
        stack_unit = -6.4 * max(2, 54.4 - 4.48 * cs) / 64
        stack_offset = (obj.get('stack_height', 0) if obj is not None else 0) * stack_unit
        target = None
        head_target = None
        target_velocity = (0.0, 0.0)
        target_accel = (0.0, 0.0)
        tracking = False
        time_until = obj['time'] - live if obj else 0
        if obj is not None and obj['type'] & 3:
            ox, oy = obj['x'], obj['y']
            head_target = (x + (ox + stack_offset) * width / 512,
                           y + ((384 - oy if mods & 16 else oy) + stack_offset) * height / 384)
            path = obj.get('slider_path')
            if (path is not None and obj['time'] <= live <= obj['time'] + obj['duration']
                    and ((id(obj) in self.reached_notes and (not self.auto_clicks or id(obj) in self.clicked_notes))
                         or live > obj['time'] + self.late_window_ms)):
                rate = playback_rate(frame)
                window_ms = min(12, max(2, self.physics.config.smooth_ms / 8))
                soften = self.physics.config.slider_smooth_ms > 0
                if soften:
                    speed = path.length * obj['slider_repeats'] / max(.001,obj['duration'])
                    window_ms = max(.25,min(self.physics.config.slider_smooth_ms*rate*2,
                                           hit_radius/scale*1.5/max(.001,speed)))
                drift = 0
                if not self.physics.config.require_motion:
                    drift = hit_radius/scale * 1.1 * self.physics.config.curvature * min(1,obj['duration']/rate/400)
                    extent = getattr(path, 'extent', None)
                    if isinstance(extent, (int,float)):
                        drift *= min(1, extent/max(1,hit_radius/scale*2))
                    drift *= -1 if int(obj['time']) % 2 else 1
                position, velocity, acceleration = slider_motion(path, live - obj['time'], obj['duration'],
                    obj['slider_repeats'], window_ms, soften=soften, drift=drift,
                    relax_radius=hit_radius/scale if not self.physics.config.require_motion else 0)
                ox, oy = position
                transform = (width / 512, height / 384 * (-1 if mods & 16 else 1))
                target_velocity = tuple(velocity[i] * transform[i] * rate for i in (0, 1))
                target_accel = tuple(acceleration[i] * transform[i] * rate * rate for i in (0, 1))
                tracking, time_until = True, 0
            target_y = 384 - oy if mods & 16 else oy
            candidate = x + (ox + stack_offset) * width / 512, y + (target_y + stack_offset) * height / 384
            if window[2] <= candidate[0] < window[4] and window[3] <= candidate[1] < window[5]:
                target = candidate
        cursor = self.cursor()
        self.observed_cursor = cursor
        if not (window[2] <= cursor[0] < window[4] and window[3] <= cursor[1] < window[5]):
            self.status('cursor outside osu! window')
            self.reset()
            return
        guide, following = target, False
        cfg = self.physics.config
        gap = target is not None and time_until > cfg.timing_ms and not cfg.require_motion and cfg.coast_ms > 0
        if gap and self.physics.last_output is not None and cursor != self.physics.last_output:
            self.gap_yield_until = now + .15
        gap_free = gap and now < self.gap_yield_until
        if gap_free:
            guide = None
            self.physics.velocity = (0,0)
        if target is not None and obj['type'] & 1 and not cfg.require_motion and cfg.curvature > 0:
            rng = random.Random(str((obj['time'],obj['x'],obj['y'])))
            angle = rng.uniform(0,2*math.pi)
            radius = hit_radius * .2 * cfg.curvature * rng.uniform(.3,1)
            gap_ms = obj.get('next_aim',(0,0,120*self.current_rate))[2] / self.current_rate
            radius *= max(0,min(1,gap_ms/120))
            target = (target[0]+math.cos(angle)*radius,target[1]+math.sin(angle)*radius)
            if not gap_free:
                guide = target
        if (target is not None and not tracking and not cfg.require_motion and (cfg.curvature > 0 or cfg.wiggle > 0)
                and not gap_free and 0 < time_until and (time_until <= cfg.timing_ms or gap)
                and math.dist(cursor, target) <= cfg.radius * scale):
            key = (obj['time'], target, self.bounds, mods, self.current_rate)
            rate = playback_rate(frame)
            if self.jump_key != key:
                next_note = obj.get('next_aim')
                next_object = obj.get('next_aim_object')
                next_stack_offset = next_object.get('stack_height', 0) * stack_unit if next_object is not None else 0
                if path is not None and obj['type'] & 2:
                    px, py = path.position(min(10, obj['duration']), obj['duration'], obj['slider_repeats'])
                    next_note = (px, py, min(10, obj['duration']))
                    next_stack_offset = stack_offset
                if next_note is not None:
                    nx, ny, gap = next_note
                    next_note = (x + (nx + next_stack_offset) * width / 512,
                                 y + ((384 - ny if mods & 16 else ny) + next_stack_offset) * height / 384, gap)
                if (cfg.idle_dance or cfg.coast_ms > 0) and (next_note is None or next_note[2] > cfg.timing_ms):
                    # Arrive with a gentle exit tangent so the waiting loop does not start from a stop.
                    destination = next_note[:2] if next_note is not None else (x + width / 2, y + height / 2)
                    direction = tuple(destination[i] - target[i] for i in (0, 1))
                    length = math.hypot(*direction)
                    if not length:
                        direction, length = (0, 1), 1
                    radius = min(width, height) * .26 * max(cfg.curvature, cfg.wiggle*.25)
                    next_note = (target[0] + direction[0] / length * radius,
                                 target[1] + direction[1] / length * radius, 250 * rate)
                duration = time_until / 1000 / rate
                distance = math.dist(cursor, target)
                # Reduce decorative detours when the available travel budget is tight.
                curve_budget = max(0, min(1, (cfg.max_speed*scale*duration/max(1, distance)-1.5)/1.5,
                                            (cfg.max_accel*scale*duration**2/max(1, distance)-6)/6))
                spare_speed = max(0, cfg.max_speed * scale - 2 * distance / duration)
                spare_accel = max(0, cfg.max_accel * scale - 10 * distance / duration**2)
                wiggle_limit = min(spare_speed * duration / 16, spare_accel * duration**2 / 300)
                if duration < .12 or distance < hit_radius * 2:
                    # Short segments turn even smooth per-jump waves into visible high-frequency shaking.
                    wiggle_limit = 0
                self.jump_path = JumpPath(cursor, target, tuple(v / rate for v in self.physics.velocity),
                                          next_note, time_until, cfg.curvature*curve_budget,
                                          wiggle=0 if obj['type'] & 2 else cfg.wiggle,
                                          wiggle_limit=wiggle_limit)
                self.jump_key, self.jump_started = key, live
                self.jump_points = [self.jump_path.sample(time_until * i / 32)[0] for i in range(33)]
            guide, target_velocity = self.jump_path.sample(live - self.jump_started)
            target_accel = self.jump_path.acceleration(live - self.jump_started)
            clamped = (max(window[2], min(window[4]-1, guide[0])),
                       max(window[3], min(window[5]-1, guide[1])))
            target_velocity = tuple(target_velocity[i] * rate if clamped[i] == guide[i] else 0 for i in (0, 1))
            target_accel = tuple(target_accel[i] * rate * rate if clamped[i] == guide[i] else 0 for i in (0, 1))
            guide, following = clamped, True
        else:
            self.jump_key = self.jump_path = None
            self.jump_points = []
        idle = (cfg.idle_dance and not cfg.require_motion and cfg.curvature > 0
                and (obj is None or time_until > cfg.timing_ms or (obj['type'] & 8 and time_until > 0)))
        if idle:
            if self.idle_path is None:
                self.idle_path = IdlePath(cursor, self.physics.velocity, self.bounds, cfg.curvature)
                self.idle_started = now
            guide, target_velocity, target_accel = self.idle_path.sample((now - self.idle_started) * 1000)
            clamped = (max(x, min(x + width, guide[0])), max(y, min(y + height, guide[1])))
            target_velocity = tuple(target_velocity[i] if clamped[i] == guide[i] else 0 for i in (0, 1))
            target_accel = tuple(target_accel[i] if clamped[i] == guide[i] else 0 for i in (0, 1))
            guide, following = clamped, True
        else:
            self.idle_path = None
        motion_time = min(time_until,cfg.timing_ms) if following else time_until
        moved = self.physics.step(cursor, guide, 0 if idle else motion_time,
                                  hit_radius, now, now - previous if previous is not None else 0, scale,
                                  tracking=tracking, target_velocity=target_velocity, following=following,
                                  target_accel=target_accel, late_window_ms=self.late_window_ms)
        if idle:
            self.physics.reason = 'idle skating'
        debug_tick = self.debug and now - self.last_debug >= 1
        if self.debug:
            tick_ms = (now-previous)*1000 if previous is not None else 0
            self.debug_max_tick_ms = max(self.debug_max_tick_ms, tick_ms)
            correction = math.dist(cursor,(max(window[2],min(window[4]-1,moved[0])),max(window[3],min(window[5]-1,moved[1]))))
            if correction > self.debug_max_step_px:
                self.debug_max_step_px = correction
                self.debug_correction_tick_ms = tick_ms
            reason = self.physics.reason
            self.debug_counts[reason] = self.debug_counts.get(reason, 0) + 1
            self.debug_moves += moved != cursor
        if debug_tick:
            print(f'[*] Aim: {self.physics.reason}; cursor={cursor}; target={target}; '
                  f'offset=({self.physics.offset[0]:.1f}, {self.physics.offset[1]:.1f})')
            print(f'[*] Aim interval: moves={self.debug_moves}; states={self.debug_counts}')
            print(f'[*] Aim cadence: max_tick={self.debug_max_tick_ms:.2f}ms; max_correction={self.debug_max_step_px:.2f}px; correction_tick={self.debug_correction_tick_ms:.2f}ms; rate={self.current_rate:g}x')
            self.debug_max_tick_ms = self.debug_max_step_px = 0
            self.debug_correction_tick_ms = 0
            self.debug_counts.clear()
            self.debug_moves = 0
            self.last_debug = now
        moved = (max(window[2], min(window[4] - 1, moved[0])),
                 max(window[3], min(window[5] - 1, moved[1])))
        if moved != cursor:
            if self.geometry() != window:
                self.reset()
                return
            if not self.user32.SetCursorPos(*moved):
                self.reset()
                raise ctypes.WinError(ctypes.get_last_error())
            if debug_tick:
                observed = self.cursor()
                print(f'[*] Aim Windows output: requested={moved}; observed={observed}')
        # The baseline is our write, never a readback that can already include new hand input.
        self.physics.last_output = moved
        self.near_note = None
        if obj is not None and head_target is not None:
            if math.dist(cursor, head_target) <= hit_radius * cfg.click_margin:
                self.ready_since.setdefault(id(obj), now)
            else:
                self.ready_since.pop(id(obj), None)
        if obj is not None and -self.late_window_ms <= obj['time'] - live <= 10:
            # A commanded position is not proof the cursor has arrived; observe it on the next tick.
            if head_target is not None and math.dist(cursor, head_target) <= hit_radius:
                self.near_note = id(obj)
                self.reached_notes.add(id(obj))
        path = obj.get('slider_path') if obj is not None else None
        cache_key = (path, self.bounds, bool(mods & 16), stack_offset)
        if cache_key != self.path_cache_key:
            points = []
            if path is not None:
                samples = path.points
                for px, py in samples[::max(1, math.ceil(len(samples) / 160))]:
                    points.append([x + (px + stack_offset) * width / 512,
                                   y + ((384 - py if mods & 16 else py) + stack_offset) * height / 384])
            self.path_cache_key, self.path_cache = cache_key, points
        self.telemetry = dict(active=True, reason=self.physics.reason, window=list(window[2:]),
                              bounds=list(self.bounds), cursor=list(moved), hand=list(self.physics.hand or cursor),
                              target=guide if idle else target, path=[] if idle else self.jump_points if following else self.path_cache,
                              idle=idle, radius=self.physics.config.radius * scale,
                              max_offset=self.physics.config.max_offset * scale,
                              offset=list(self.physics.offset), tracking=tracking,
                              time_until=time_until, hit_radius=hit_radius, note_head=head_target,
                              expired_notes=len(self.expired_notes), live_ms=live)
        self.telemetry.update(timing_correction_ms=self.timing_correction_ms, hit_error_median_ms=self.hit_error_median_ms)
