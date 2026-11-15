"""Sampled osu! slider geometry, fitted to the beatmap's declared pixel length."""

import bisect
import math


def slider_motion(path, elapsed, duration, repeats, window_ms, soften=False, drift=0, relax_radius=0):
    """Average the tangent around corners without changing the map's actual path."""
    normal = (0, 0)
    if drift:
        delta = tuple(path.points[-1][i]-path.points[0][i] for i in (0,1))
        length = math.hypot(*delta)
        if not length:
            delta = tuple(path.points[1][i]-path.points[0][i] for i in (0,1))
            length = math.hypot(*delta)
        if length:
            normal = (-delta[1]/length, delta[0]/length)
    def sample(time):
        position = path.position(time, duration, repeats)
        if soften:
            before = path.position(max(0,time-window_ms), duration, repeats)
            after = path.position(min(duration,time+window_ms), duration, repeats)
            position = tuple((before[i]+2*position[i]+after[i])*.25 for i in (0,1))
        if relax_radius and isinstance(getattr(path,'extent',None),(int,float)) and path.extent <= relax_radius*.75:
            # A compact slider fits under the cursor: avoid reproducing every tiny reversal.
            position = path.points[0]
        u = max(0,min(1,time/duration))
        # One broad lateral excursion per slider, without flipping direction at repeats.
        offset = drift * 64*u**3*(1-u)**3 * math.sin(math.pi*(u+.15))
        return tuple(position[i]+normal[i]*offset for i in (0,1))
    position = sample(elapsed)
    left, right = max(0, elapsed - window_ms), min(duration, elapsed + window_ms)
    before, after = sample(left), sample(right)
    if right <= left:
        return position, (0, 0), (0, 0)
    velocity = tuple((after[i] - before[i]) * 1000 / (right - left) for i in (0, 1))
    acceleration = (0, 0)
    if left < elapsed < right:
        acceleration = tuple(2 * ((after[i] - position[i]) / (right - elapsed)
                                  - (position[i] - before[i]) / (elapsed - left))
                             * 1000000 / (right - left) for i in (0, 1))
    return position, velocity, acceleration


def bezier(points):
    if len(points) > 128:
        raise ValueError('Too many controls in one Bezier segment')
    # shortcut: fixed sampling approximates curves; use adaptive subdivision if errors become visible.
    result = []
    for sample in range(65):
        t = sample / 64
        work = list(points)
        while len(work) > 1:
            work = [tuple((1 - t) * a[k] + t * b[k] for k in (0, 1))
                    for a, b in zip(work, work[1:])]
        result.append(work[0])
    return result


def curve_points(kind, controls):
    if kind == 'L':
        return controls
    if kind == 'P' and len(controls) == 3:
        a, b, c = controls
        cross = 2 * (a[0] * (b[1] - c[1]) + b[0] * (c[1] - a[1]) + c[0] * (a[1] - b[1]))
        if abs(cross) > 1e-6:
            lengths = [x * x + y * y for x, y in controls]
            ux = sum(lengths[i] * (controls[(i + 1) % 3][1] - controls[(i + 2) % 3][1]) for i in range(3)) / cross
            uy = sum(lengths[i] * (controls[(i + 2) % 3][0] - controls[(i + 1) % 3][0]) for i in range(3)) / cross
            angles = [math.atan2(y - uy, x - ux) for x, y in controls]
            sweep = (angles[2] - angles[0]) % math.tau
            if (angles[1] - angles[0]) % math.tau > sweep:
                sweep -= math.tau
            radius = math.dist(a, (ux, uy))
            return [(ux + radius * math.cos(angles[0] + sweep * i / 128),
                     uy + radius * math.sin(angles[0] + sweep * i / 128)) for i in range(129)]
        return controls
    if kind == 'C':
        result = []
        for i in range(len(controls) - 1):
            p0 = controls[max(0, i - 1)]
            p1, p2 = controls[i:i + 2]
            p3 = controls[min(len(controls) - 1, i + 2)]
            for sample in range(33):
                t = sample / 32
                result.append(tuple(.5 * (2 * p1[k] + (-p0[k] + p2[k]) * t
                                    + (2 * p0[k] - 5 * p1[k] + 4 * p2[k] - p3[k]) * t * t
                                    + (-p0[k] + 3 * p1[k] - 3 * p2[k] + p3[k]) * t ** 3)
                                    for k in (0, 1)))
        return result
    if kind not in ('B', 'P'):
        raise ValueError('Unsupported slider curve')
    result, segment = [], []
    for i, point in enumerate(controls):
        segment.append(point)
        if i == len(controls) - 1 or point == controls[i + 1]:
            result.extend(bezier(segment))
            segment = []
    return result


class SliderPath:
    def __init__(self, start, encoded, length):
        parts = encoded.split('|')
        controls = [tuple(start)] + [tuple(map(float, value.split(':'))) for value in parts[1:]]
        if (len(controls) < 2 or len(controls) > 2048 or not math.isfinite(length) or length <= 0
                or any(len(p) != 2 or not all(math.isfinite(v) for v in p) for p in controls)):
            raise ValueError('Invalid slider path')
        sampled = curve_points(parts[0], controls)
        self.points, self.distances = [sampled[0]], [0.0]
        for point in sampled[1:]:
            distance = math.dist(point, self.points[-1])
            if distance < 1e-9:
                continue
            remaining = length - self.distances[-1]
            if distance >= remaining:
                previous = self.points[-1]
                self.points.append(tuple(previous[k] + (point[k] - previous[k]) * remaining / distance for k in (0, 1)))
                self.distances.append(length)
                break
            self.points.append(point)
            self.distances.append(self.distances[-1] + distance)
        if len(self.points) < 2:
            raise ValueError('Zero-length slider path')
        if self.distances[-1] < length:
            a, b = self.points[-2:]
            factor = (length - self.distances[-1]) / math.dist(a, b)
            self.points.append(tuple(b[k] + (b[k] - a[k]) * factor for k in (0, 1)))
            self.distances.append(length)
        self.length = length
        self.extent = math.hypot(*(max(p[i] for p in self.points)-min(p[i] for p in self.points) for i in (0,1)))

    def position(self, elapsed, duration, repeats):
        if duration <= 0 or repeats < 1:
            raise ValueError('Invalid slider duration or repeats')
        progress = max(0, min(1, elapsed / duration)) * repeats
        lap = min(repeats - 1, int(progress))
        fraction = progress - lap
        if lap % 2:
            fraction = 1 - fraction
        distance = max(0, min(self.length, fraction * self.length))
        index = min(len(self.points) - 1, max(1, bisect.bisect_left(self.distances, distance)))
        a, b = self.points[index - 1:index + 1]
        t = (distance - self.distances[index - 1]) / (self.distances[index] - self.distances[index - 1])
        return tuple(a[k] + (b[k] - a[k]) * t for k in (0, 1))
