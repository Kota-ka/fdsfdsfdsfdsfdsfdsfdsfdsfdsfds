"""Deterministic replay of the actual aim/input pipeline; all OS inputs are mocked."""

import math
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import aim
import tosu
from aim_physics import AimConfig


def replay_stream(bpm, jump=False, max_speed=16000, max_accel=1000000, rate=1.5, variable=False, client='stable', trace=None):
    count = 24
    interval = 60000/bpm/4*rate
    objects = []
    for i in range(count):
        x = (100 if i % 2 == 0 else 412) if jump else 100+i*7
        y = (150 if i % 2 == 0 else 230) if jump else 192+25*math.sin(i*.3)
        timestamp = 300 if not objects else objects[-1]['time'] + (60000/(120,240,360,180)[i%4]/4*rate if variable else interval)
        objects.append(dict(time=timestamp, duration=0, type=1, x=x, y=y))
    clock = [0.0]
    position = [384+objects[0]['x']*2.25,126+(384-objects[0]['y'])*2.25]
    history, judgements = {}, []
    engine = SimpleNamespace(hit_objects=objects, approach_rate=9.5, stack_leniency=.6, format_version=14)
    with patch.object(aim.ctypes,'WinDLL'), patch.object(aim.time,'monotonic',side_effect=lambda:clock[0]), \
            patch.object(tosu,'keyboard') as keyboard, patch.object(tosu,'OsuRelaxCheatsEngine',return_value=engine):
        assist = aim.AimAssist(AimConfig(strength=1, radius=740, max_offset=640,
            max_speed=max_speed, max_accel=max_accel, hand_gain=.35, curvature=1, wiggle=.8,
            damping_ratio=.6, coast_ms=150, click_settle_ms=5, click_margin=1,
            require_motion=False, on_note_hold=False))
        assist.window = (1,2,0,0,1920,1080)
        assist.bounds, assist.manual = (384,126,1152,864), True
        if client == 'lazer':
            assist.bounds, assist.manual = None, False
        assist.geometry = Mock(return_value=assist.window)
        assist.cursor = lambda:tuple(position)
        assist.user32.GetForegroundWindow.return_value = 1
        def move(x,y):
            position[:] = [x,y]
            return True
        assist.user32.SetCursorPos.side_effect = move
        assist.motion_thread = Mock()
        player = tosu.Playback(assist)
        player.auto_timing = False
        def pressed(key):
            obj = objects[player.index-1]
            offset = -.1*(54.4-4.48*5.2)*obj.get('stack_height',0)
            head = (384+(obj['x']+offset)*2.25,126+(384-obj['y']+offset)*2.25)
            game_cursor = history.get(round(clock[0]*1000)-5,tuple(position))
            error = clock[0]*1000-obj['time']/rate
            judgements.append((error,math.dist(game_cursor,head)<=(54.4-4.48*5.2)*2.25))
        keyboard.press.side_effect = pressed
        try:
            for tick in range(math.ceil(objects[-1]['time']/rate)+100):
                clock[0] = tick/1000
                live = tick*rate
                if tick % 8 == 0:
                    frame = dict(client=client,state={'number':2},game={'focused':True,'paused':False},
                        play={'failed':False,'mode':{'number':0},'mods':{'number':600,'rate':rate}},
                        beatmap={'time':{'live':live},'stats':{'cs':{'converted':5.2},'hitWindow':{'great':13,'meh':66.333333}}},
                        folders={'songs':os.path.abspath('Songs')},directPath={'beatmapFile':'test.osu'},
                        settings={'keybinds':{'osu':{'k1':'z','k2':'x'}},'mouse':{'rawInput':False}})
                    player.update(frame,sampled_at=clock[0])
                if assist.latest_model is not None:
                    obj = assist.latest_model[0]
                    while obj is not None and assist.motion_can_advance(obj,live):
                        obj = obj.get('next_aim_object')
                    assist._step(obj,live,frame)
                    player.input_tick(live)
                history[tick] = tuple(position)
                if trace is not None:
                    trace.append((tick,tuple(position),objects))
        finally:
            player.release()
            assist.motion_thread = None
    return count, judgements


class StreamReplayTests(unittest.TestCase):
    def test_lazer_automatic_projection_with_variable_spacing(self):
        count, hits = replay_stream(240,rate=1.2,variable=True,client='lazer')
        self.assertEqual(len(hits),count)
        self.assertTrue(all(inside and abs(error)<=13 for error,inside in hits),hits)
    def test_variable_spacing_at_custom_slow_and_fast_rates(self):
        for rate in (.75,1.2,1.75):
            with self.subTest(rate=rate):
                count, hits = replay_stream(240,rate=rate,variable=True)
                self.assertEqual(len(hits),count)
                self.assertTrue(all(inside and abs(error)<=13 for error,inside in hits),hits)
    def test_streams_with_nc_hr_and_five_ms_cursor_sampling_lag(self):
        for bpm in (200,240,300,360):
            with self.subTest(bpm=bpm):
                count, hits = replay_stream(bpm)
                self.assertEqual(len(hits), count)
                self.assertTrue(all(inside and abs(error)<=13 for error,inside in hits), hits)

    def test_dense_jumps_with_nc_hr_and_cursor_sampling_lag(self):
        for bpm in (200,240,300,360):
            with self.subTest(bpm=bpm):
                count, hits = replay_stream(bpm, jump=True)
                self.assertEqual(len(hits), count)
                self.assertTrue(all(inside and abs(error)<=13 for error,inside in hits), hits)
