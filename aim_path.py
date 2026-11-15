"""Timed jump curves with endpoint velocities and a smooth lateral bow."""

import math
import random

LATE_NOTE_MS = 50


class JumpPath:
    def __init__(self, start, end, velocity, following, duration_ms, curvature,
                 wiggle=0, seed=None, wiggle_limit=None):
        self.start, self.end = tuple(start), tuple(end)
        self.duration = max(.001, duration_ms / 1000)
        delta = tuple(end[i] - start[i] for i in (0, 1))
        distance = math.hypot(*delta)
        self.normal = (-delta[1] / distance, delta[0] / distance) if distance else (0, 0)
        speed = math.hypot(*velocity)
        factor = min(1, 1.6 * distance / (speed * self.duration)) if speed else 1
        self.incoming = tuple(value * factor for value in velocity)
        self.outgoing = (0, 0)
        self.bow_skew, bend = 0, 1
        cross = delta[0] * velocity[1] - delta[1] * velocity[0]
        if following is not None:
            next_delta = tuple(following[i] - end[i] for i in (0, 1))
            length = math.hypot(*next_delta)
            if length:
                direction = tuple(delta[i] / distance + next_delta[i] / length for i in (0, 1)) if distance else (0, 0)
                if distance:
                    alignment = sum(delta[i] / distance * next_delta[i] / length for i in (0, 1))
                    bend = .35 + .65 * math.sqrt(max(0, (1-alignment)/2))
                    reversal = max(0, min(1, (-alignment - .5) * 2)) * curvature
                    # Cross the hit point before rounding a reversal, rather than aiming down its bisector.
                    direction = tuple(direction[i] - next_delta[i] / length * reversal for i in (0, 1))
                norm = math.hypot(*direction)
                next_cross = delta[0] * next_delta[1] - delta[1] * next_delta[0]
                turn_gain = 1
                if abs(next_cross) > 1e-6:
                    cross = next_cross
                if norm < .25:
                    # Carry momentum around a U-turn instead of stopping at the note.
                    direction = tuple(value * (-1 if cross < 0 else 1) for value in self.normal)
                    norm = 1
                    turn_gain = .15 + .85 * curvature
                entry_speed = distance / self.duration
                exit_speed = length / max(.001, following[2] / 1000)
                self.bow_skew = .65 * (exit_speed-entry_speed) / max(1, exit_speed+entry_speed)
                speed = 2.1 * entry_speed * exit_speed / max(1, entry_speed+exit_speed) * turn_gain
                if norm:
                    self.outgoing = tuple(value / norm * speed for value in direction)
        self.bow = distance * .12 * curvature * bend * (-1 if cross < 0 else 1)
        self.wiggles = []
        self.wiggle_style = 'off'
        if wiggle > 0 and distance > 0 and (wiggle_limit is None or wiggle_limit > 0):
            rng = random.Random(seed)
            if rng.random() >= .25:
                self.wiggle_style = 'arc'
                return
            amplitude = distance * .6 * wiggle
            if wiggle_limit is not None:
                amplitude = min(amplitude, max(0, wiggle_limit))
            tangent = tuple(value / distance for value in delta)
            frequency = rng.uniform(.35, .65)
            self.wiggle_style = rng.choice(('sweep', 'loop', 'ribbon'))
            phase = rng.uniform(-math.pi, math.pi)
            if self.wiggle_style == 'loop':
                terms = ((tangent, .85, frequency, phase),
                         (self.normal, .85, frequency, phase+math.pi/2),
                         (self.normal, .1, frequency+.35, rng.uniform(-math.pi, math.pi)))
            elif self.wiggle_style == 'ribbon':
                terms = ((tangent, .4, frequency, phase),
                         (self.normal, .8, frequency*1.2, rng.uniform(-math.pi, math.pi)),
                         (self.normal, .15, .7, phase))
            else:
                terms = ((tangent, .35, frequency, phase),
                         (self.normal, 1, frequency, rng.uniform(-math.pi, math.pi)),
                         (self.normal, .12, frequency+.35, rng.uniform(-math.pi, math.pi)))
            for direction, weight, cycles, phase in terms:
                self.wiggles.append((tuple(v * amplitude * weight for v in direction),
                                     2 * math.pi * cycles, phase))

    def _bow(self, u):
        envelope = 64*u**3*(1-u)**3
        first = 192*u*u*(1-u)**2*(1-2*u)
        second = 384*u*(1-u)*(1-5*u+5*u*u)
        shape, slope = 1+self.bow_skew*(2*u-1), 2*self.bow_skew
        return (self.bow*envelope*shape,
                self.bow*(first*shape+envelope*slope)/self.duration,
                self.bow*(second*shape+2*first*slope)/self.duration**2)

    def _wiggle(self, u):
        envelope = 64*u**3*(1-u)**3
        first = 192*u*u*(1-u)**2*(1-2*u)
        second = 384*u*(1-u)*(1-5*u+5*u*u)
        position, velocity, acceleration = [0., 0.], [0., 0.], [0., 0.]
        for amplitude, frequency, phase in self.wiggles:
            sine, cosine = math.sin(frequency*u+phase), math.cos(frequency*u+phase)
            for i in (0, 1):
                position[i] += amplitude[i] * envelope * sine
                velocity[i] += amplitude[i] * (first*sine + envelope*frequency*cosine) / self.duration
                acceleration[i] += amplitude[i] * (second*sine + 2*first*frequency*cosine
                                                   - envelope*frequency**2*sine) / self.duration**2
        return position, velocity, acceleration

    def sample(self, elapsed_ms):
        u = max(0, min(1, elapsed_ms / 1000 / self.duration))
        # Quintic Hermite basis keeps acceleration zero at both joins.
        h = (1-10*u**3+15*u**4-6*u**5, u-6*u**3+8*u**4-3*u**5,
             10*u**3-15*u**4+6*u**5, -4*u**3+7*u**4-3*u**5)
        dh = (-30*u*u+60*u**3-30*u**4, 1-18*u*u+32*u**3-15*u**4,
              30*u*u-60*u**3+30*u**4, -12*u*u+28*u**3-15*u**4)
        bow, bow_velocity, _ = self._bow(u)
        position, velocity = [], []
        wiggle_position, wiggle_velocity, _ = self._wiggle(u)
        for i in (0, 1):
            values = (self.start[i], self.incoming[i] * self.duration,
                      self.end[i], self.outgoing[i] * self.duration)
            position.append(sum(h[j] * values[j] for j in range(4)) + self.normal[i] * bow + wiggle_position[i])
            velocity.append(sum(dh[j] * values[j] for j in range(4)) / self.duration + self.normal[i] * bow_velocity + wiggle_velocity[i])
        return tuple(position), tuple(velocity)

    def acceleration(self, elapsed_ms):
        u = max(0, min(1, elapsed_ms / 1000 / self.duration))
        ddh = (-60*u+180*u*u-120*u**3, -36*u+96*u*u-60*u**3,
               60*u-180*u*u+120*u**3, -24*u+84*u*u-60*u**3)
        _, _, bow_acceleration = self._bow(u)
        result = []
        _, _, wiggle_acceleration = self._wiggle(u)
        for i in (0, 1):
            values = (self.start[i], self.incoming[i] * self.duration,
                      self.end[i], self.outgoing[i] * self.duration)
            result.append(sum(ddh[j] * values[j] for j in range(4)) / self.duration**2
                          + self.normal[i] * bow_acceleration + wiggle_acceleration[i])
        return tuple(result)


class IdlePath:
    """Blend incoming momentum into a bounded ribbon-shaped orbit during gaps."""

    def __init__(self, start, velocity, bounds, amount):
        self.start, self.velocity = tuple(start), tuple(velocity)
        x, y, width, height = bounds
        self.radius = min(width, height) * .26 * amount
        rx, ry = self.radius * 1.18, self.radius * .77
        self.center = (max(x + rx, min(x + width - rx, start[0])),
                       max(y + ry, min(y + height - ry, start[1])))
        self.phase = math.atan2(-velocity[0], velocity[1]) if math.hypot(*velocity) else 0

    def sample(self, elapsed_ms):
        t = max(0, elapsed_ms / 1000)
        omega = 2 * math.pi / 2.4
        phase = self.phase + omega * t
        r, cx, cy = self.radius, *self.center
        orbit = (cx + r * (math.cos(phase) + .18 * math.sin(2*phase)),
                 cy + r * (.65 * math.sin(phase) + .12 * math.sin(3*phase)))
        orbit_v = (r * omega * (-math.sin(phase) + .36 * math.cos(2*phase)),
                   r * omega * (.65 * math.cos(phase) + .36 * math.cos(3*phase)))
        orbit_a = (-r * omega**2 * (math.cos(phase) + .72 * math.sin(2*phase)),
                   -r * omega**2 * (.65 * math.sin(phase) + 1.08 * math.sin(3*phase)))
        u = min(1, t / .45)
        blend = 6*u**5 - 15*u**4 + 10*u**3
        blend_v = (30*u**4 - 60*u**3 + 30*u*u) / .45
        blend_a = (120*u**3 - 180*u*u + 60*u) / .45**2
        z, tau = t / .18, .18
        denominator = 1 + z*z
        position, velocity, acceleration = [], [], []
        for i in (0, 1):
            coast = self.start[i] + self.velocity[i] * t / denominator
            coast_v = self.velocity[i] * (1-z*z) / denominator**2
            coast_a = self.velocity[i] * (2*t/tau**2) * (z*z-3) / denominator**3
            position.append((1-blend)*coast + blend*orbit[i])
            velocity.append((1-blend)*coast_v + blend*orbit_v[i] + blend_v*(orbit[i]-coast))
            acceleration.append((1-blend)*coast_a + blend*orbit_a[i]
                                + 2*blend_v*(orbit_v[i]-coast_v) + blend_a*(orbit[i]-coast))
        return tuple(position), tuple(velocity), tuple(acceleration)
