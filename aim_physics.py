"""Hand plus fractional offset model, adapted from lame-osu's aim-assist design."""

from dataclasses import dataclass
import math
from aim_path import LATE_NOTE_MS

AIM_LIMITS = {
    'strength': (0, 1), 'smooth_ms': (1, 2000), 'radius': (1, 1024),
    'timing_ms': (1, 2000), 'cone_deg': (0, 360),
    'return_close_ms': (1, 2000), 'return_far_ms': (1, 2000),
    'motion_curve': (0.1, 8), 'max_speed': (1, 20000),
    'max_offset': (1, 640), 'max_accel': (1, 1000000),
    'hand_gain': (0, 1),
    'curvature': (0, 1),
    'wiggle': (0, 1), 'click_margin': (.5, 1),
    'damping_ratio': (.35, 1), 'coast_ms': (0, 500), 'click_settle_ms': (0, 20),
    'slider_smooth_ms': (0, 60),
}

AIM_PROFILES = {
    'assist': {},
    'strong': dict(strength=1, smooth_ms=65, radius=640, timing_ms=400,
                   cone_deg=180, max_speed=16000, max_offset=640, max_accel=1000000,
                   return_close_ms=60, return_far_ms=60, hand_gain=.35, curvature=.65,
                   damping_ratio=.6, coast_ms=150, click_settle_ms=5, slider_smooth_ms=24),
    # Preserve old commands without restoring autonomous full-cursor control.
    'aimbot': dict(strength=1, smooth_ms=65, radius=640, timing_ms=400,
                   cone_deg=180, max_speed=16000, max_offset=640, max_accel=1000000,
                   return_close_ms=60, return_far_ms=60, hand_gain=.35, curvature=.65,
                   damping_ratio=.6, coast_ms=150, click_settle_ms=5, slider_smooth_ms=24),
}


@dataclass
class AimConfig:
    strength: float = 0.65
    smooth_ms: float = 90
    radius: float = 100
    timing_ms: float = 220
    cone_deg: float = 90
    return_close_ms: float = 140
    return_far_ms: float = 180
    motion_curve: float = 1
    max_speed: float = 300
    max_offset: float = 24
    max_accel: float = 1800
    hand_gain: float = 1
    curvature: float = 0
    wiggle: float = 0
    click_margin: float = .9
    damping_ratio: float = 1
    coast_ms: float = 0
    click_settle_ms: float = 0
    slider_smooth_ms: float = 0
    idle_dance: bool = False
    require_motion: bool = True
    on_note_hold: bool = True
    tablet: bool = False

    def __post_init__(self):
        for name, (lower, upper) in AIM_LIMITS.items():
            value = getattr(self, name)
            if not math.isfinite(value) or not lower <= value <= upper:
                raise ValueError(f'{name} must be between {lower} and {upper}')


def bounded_delta(offset, delta, limit):
    """Clip the new correction along its direction, without scaling old motion backwards."""
    if math.hypot(*(offset[i] + delta[i] for i in (0, 1))) <= limit:
        return delta
    length2 = sum(value * value for value in delta)
    if length2 < 1e-12:
        return (0.0, 0.0)
    dot = sum(offset[i] * delta[i] for i in (0, 1))
    remaining = max(0, limit * limit - sum(value * value for value in offset))
    fraction = max(0, min(1, (-dot + math.sqrt(dot * dot + length2 * remaining)) / length2))
    return tuple(value * fraction for value in delta)


class AssistPhysics:
    def __init__(self, config):
        self.config = config
        self.reset()

    def reset(self):
        self.hand = None
        self.last_output = None
        self.offset = (0.0, 0.0)
        self.velocity = (0.0, 0.0)
        self.direction = (0.0, 0.0)
        self.raw_position = None
        self.last_motion = (0.0, 0.0)
        self.last_motion_time = None
        self.moving_until = 0.0
        self.reason = 'waiting for hand movement'

    def step(self, cursor, target, time_until, hit_radius, now, dt, scale, tracking=False,
             target_velocity=(0.0, 0.0), following=False, target_accel=(0.0, 0.0),
             late_window_ms=LATE_NOTE_MS):
        if self.hand is None:
            self.hand = tuple(cursor)
            self.last_output = tuple(cursor)
            self.raw_position = tuple(cursor)
            self.last_motion_time = now
            return tuple(cursor)
        cfg = self.config
        if self.raw_position is None:
            self.raw_position = self.hand
        if cfg.tablet:
            motion = tuple(cursor[i] - self.raw_position[i] for i in (0, 1)) if cursor != self.last_output else (0, 0)
            if cursor != self.last_output:
                self.raw_position = tuple(cursor)
        else:
            motion = tuple(cursor[i] - self.last_output[i] for i in (0, 1))
            self.raw_position = tuple(self.raw_position[i] + motion[i] for i in (0, 1))
        self.hand = tuple(self.hand[i] + motion[i] for i in (0, 1))
        self.last_motion = motion
        dt = max(0, min(dt, 0.032))
        if math.hypot(*motion) > max(0.05, 8 * scale * dt):
            sample_dt = max(.001, min(.025, now - self.last_motion_time if self.last_motion_time is not None else dt))
            blend = -math.expm1(-sample_dt / .025)
            self.direction = tuple(self.direction[i] + (motion[i] / sample_dt - self.direction[i]) * blend for i in (0, 1))
            self.last_motion_time = now
            self.moving_until = now + 0.06
        moving = now < self.moving_until
        assisted = tuple(self.hand[i] + self.offset[i] for i in (0, 1))
        distance = math.dist(assisted, target) if target is not None else math.inf
        in_window = target is not None and -late_window_ms <= time_until <= cfg.timing_ms
        on_note = in_window and distance <= hit_radius
        eligible = in_window and distance <= cfg.radius * scale
        self.reason = 'outside timing window' if not in_window else 'outside assist radius'
        if eligible and cfg.require_motion and not moving and (tracking or following or not (on_note and cfg.on_note_hold)):
            eligible = False
            self.reason = 'waiting for hand movement'
        angle_weight = 1.0
        if eligible and moving and not on_note and not tracking:
            to_target = tuple(target[i] - assisted[i] for i in (0, 1))
            denominator = math.hypot(*self.direction) * math.hypot(*to_target)
            cosine = sum(self.direction[i] * to_target[i] for i in (0, 1)) / denominator if denominator else 1
            cutoff = math.cos(math.radians(min(cfg.cone_deg, 180)))
            # A full 180-degree cone explicitly allows correction against hand motion.
            if cosine < cutoff or (cfg.cone_deg < 180 and sum(motion[i] * to_target[i] for i in (0, 1)) < 0):
                eligible = False
                self.reason = 'hand moving away from target'
            elif cfg.cone_deg < 180 and cutoff < 1 - 1e-9:
                weight = max(0, min(1, (cosine - cutoff) / (1 - cutoff)))
                angle_weight = weight * weight * (3 - 2 * weight)
        if eligible and cfg.strength > 0 and not (on_note and cfg.on_note_hold and not tracking and not following):
            # Attenuate only physical input during correction; retain full raw movement for diagnostics.
            assisted = tuple(assisted[i] - motion[i] * (1 - cfg.hand_gain) for i in (0, 1))
            distance = math.dist(assisted, target)
            on_note = distance <= hit_radius
        release_ms = cfg.return_close_ms if distance <= cfg.radius * scale else cfg.return_far_ms
        self.offset = tuple(value * math.exp(-dt * 1000 / release_ms) for value in self.offset)
        # Absorb past correction into the current reference; never replay its inverse to the mouse.
        self.hand = tuple(assisted[i] - self.offset[i] for i in (0, 1))
        if not eligible:
            if (cfg.coast_ms > 0 and not cfg.require_motion and cfg.strength > 0
                    and (target is None or time_until > cfg.timing_ms)
                    and math.hypot(*motion) < 2*scale):
                tau = cfg.coast_ms / 1000
                decay = math.exp(-dt / tau)
                delta = tuple(value * tau * (1-decay) for value in self.velocity)
                old_velocity = self.velocity
                wanted = tuple(value * decay for value in old_velocity)
                change = math.dist(old_velocity, wanted)
                limit = cfg.max_accel * scale * dt
                if change > limit and change:
                    wanted = tuple(old_velocity[i] + (wanted[i]-old_velocity[i])*limit/change for i in (0, 1))
                    delta = tuple((old_velocity[i]+wanted[i])*.5*dt for i in (0, 1))
                self.velocity = wanted
                delta = bounded_delta(self.offset, delta, cfg.max_offset * scale)
                self.offset = tuple(self.offset[i] + delta[i] for i in (0, 1))
                self.last_output = tuple(round(self.hand[i] + self.offset[i]) for i in (0, 1))
                self.reason = 'inertial glide'
                return self.last_output
            self.hand = tuple(cursor[i] - self.offset[i] for i in (0, 1))
            self.velocity = (0.0, 0.0)
            self.last_output = tuple(cursor)
            return self.last_output
        tau = cfg.smooth_ms
        if on_note and cfg.on_note_hold and not tracking and not following:
            pull = (0.0, 0.0)
            self.reason = 'on-note hold'
        else:
            progress = max(0, min(1, 1 - max(0, time_until) / cfg.timing_ms))
            eased = progress * progress * (3 - 2 * progress)
            # At full strength, the capture window must not silently weaken the pull.
            strength = cfg.strength * (cfg.strength + (1 - cfg.strength) * eased ** cfg.motion_curve) * angle_weight
            pull = tuple((target[i] - assisted[i]) * strength for i in (0, 1))
            if tracking and cfg.require_motion:
                length = math.hypot(*self.direction)
                if length:
                    direction = tuple(value / length for value in self.direction)
                    forward = sum(pull[i] * direction[i] for i in (0, 1))
                    pull = tuple(pull[i] - forward * direction[i] for i in (0, 1))
            self.reason = ('slider lateral correction' if cfg.require_motion else 'slider tracking') if tracking else 'assisting'
            if following:
                self.reason = 'following jump curve'
        pull = bounded_delta(self.offset, pull, cfg.max_offset * scale)
        desired = tuple(self.offset[i] + pull[i] for i in (0, 1))
        # Elastic in flight; smoothly increase damping around a timed hit point.
        omega = 4 / (tau / 1000)
        damping = cfg.damping_ratio
        if target is not None and not tracking and time_until <= 40:
            arrival = max(0, min(1, 1-max(0, time_until)/40))
            damping += (1-damping) * arrival*arrival*(3-2*arrival)
        decay = math.exp(-damping * omega * dt)
        error = tuple(desired[i] - self.offset[i] for i in (0, 1))
        # Follow the moving slider, rather than damping its forward motion toward zero.
        follow = tuple(value * cfg.strength for value in target_velocity) if (tracking or following) and not cfg.require_motion else (0.0, 0.0)
        speed = math.hypot(*follow)
        if speed > cfg.max_speed * scale:
            follow = tuple(value * cfg.max_speed * scale / speed for value in follow)
        acceleration = tuple(value * cfg.strength for value in target_accel) if (following or tracking) and not cfg.require_motion else (0, 0)
        travel = tuple(follow[i] * dt - .5 * acceleration[i] * dt * dt for i in (0, 1))
        start_error = tuple(error[i] - travel[i] for i in (0, 1))
        relative_velocity = tuple(self.velocity[i] - follow[i] + acceleration[i] * dt for i in (0, 1))
        if damping >= .999999:
            cosine, sine = 1, dt
        else:
            frequency = omega * math.sqrt(1-damping*damping)
            cosine, sine = math.cos(frequency*dt), math.sin(frequency*dt)/frequency
        spring_delta = tuple(travel[i] + start_error[i] - (start_error[i]*cosine
                             + (damping*omega*start_error[i]-relative_velocity[i])*sine)*decay for i in (0, 1))
        wanted_velocity = tuple(follow[i] + (relative_velocity[i]*cosine
                                + (omega*omega*start_error[i]-damping*omega*relative_velocity[i])*sine)*decay for i in (0, 1))
        length = math.hypot(*wanted_velocity)
        speed_limit = cfg.max_speed * scale
        if cfg.require_motion:
            speed_limit = min(speed_limit, math.hypot(*self.direction) * cfg.strength)
        constrained = math.hypot(*spring_delta) > speed_limit * dt
        if length > speed_limit:
            constrained = True
            wanted_velocity = tuple(value * speed_limit / length for value in wanted_velocity)
        acceleration_step = tuple(wanted_velocity[i] - self.velocity[i] for i in (0, 1))
        length = math.hypot(*acceleration_step)
        limit = cfg.max_accel * scale * dt
        if length > limit and length:
            constrained = True
            acceleration_step = tuple(value * limit / length for value in acceleration_step)
        old_velocity = self.velocity
        self.velocity = tuple(self.velocity[i] + acceleration_step[i] for i in (0, 1))
        delta = (tuple((old_velocity[i] + self.velocity[i]) * .5 * dt for i in (0, 1))
                 if constrained else spring_delta)
        error = tuple(desired[i] - self.offset[i] for i in (0, 1))
        if (cfg.damping_ratio == 1 and cfg.coast_ms == 0 and not following and not tracking
                and math.hypot(*delta) > math.hypot(*error) and sum(delta[i] * error[i] for i in (0, 1)) > 0):
            delta = error
            self.velocity = (0.0, 0.0)
        limited = bounded_delta(self.offset, delta, cfg.max_offset * scale)
        if limited != delta:
            self.velocity = (0.0, 0.0)
        self.offset = tuple(self.offset[i] + limited[i] for i in (0, 1))
        # Keep the fractional offset even when the Windows output rounds to the same pixel.
        self.last_output = tuple(round(self.hand[i] + self.offset[i]) for i in (0, 1))
        return self.last_output
