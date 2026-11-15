"""osu! stacking, adapted from ppy/osu's MIT-licensed OsuBeatmapProcessor."""

import math


def stack_heights(objects, threshold, version=14):
    starts = [(obj['x'], obj['y']) for obj in objects]
    ends = []
    for obj, start in zip(objects, starts):
        path = obj.get('slider_path')
        ends.append(path.position(obj['duration'], obj['duration'], obj['slider_repeats'])
                    if obj['type'] & 2 and path is not None else start)
    heights = [0] * len(objects)
    if version < 6:
        for i, obj in enumerate(objects):
            if obj['type'] & 8 or (heights[i] and not obj['type'] & 2):
                continue
            end_time = obj['time'] + obj['duration']
            tail = obj['slider_path'].points[-1] if obj.get('slider_path') is not None else starts[i]
            slider_stack = 0
            for j in range(i + 1, len(objects)):
                other = objects[j]
                if other['time'] - threshold > end_time:
                    break
                if math.dist(starts[j], starts[i]) < 3:
                    heights[i] += 1
                    end_time = other['time']
                elif math.dist(starts[j], tail) < 3:
                    slider_stack += 1
                    heights[j] -= slider_stack
                    end_time = other['time']
        return heights
    for i in range(len(objects) - 1, 0, -1):
        if heights[i] or objects[i]['type'] & 8:
            continue
        current = i
        if objects[i]['type'] & 1:
            for n in range(i - 1, -1, -1):
                obj = objects[n]
                if obj['type'] & 8:
                    continue
                if int(objects[current]['time']) - int(obj['time'] + obj['duration']) > threshold:
                    break
                if obj['type'] & 2 and math.dist(ends[n], starts[current]) < 3:
                    adjustment = heights[current] - heights[n] + 1
                    for j in range(n + 1, i + 1):
                        if math.dist(ends[n], starts[j]) < 3:
                            heights[j] -= adjustment
                    break
                if math.dist(starts[n], starts[current]) < 3:
                    heights[n] = heights[current] + 1
                    current = n
        elif objects[i]['type'] & 2:
            for n in range(i - 1, -1, -1):
                if objects[n]['type'] & 8:
                    continue
                if objects[current]['time'] - objects[n]['time'] > threshold:
                    break
                if math.dist(ends[n], starts[current]) < 3:
                    heights[n] = heights[current] + 1
                    current = n
    return heights
