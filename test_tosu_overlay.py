from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from aim_physics import AimConfig
from tosu_overlay import TosuOverlay, native_layout, schema, validate_settings


class OverlayTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.aim = SimpleNamespace(physics=SimpleNamespace(config=AimConfig(), offset=(0, 0)), enabled=True,
                                   hold_key=None, debug=False, reset=Mock(),
                                   motion_lock=threading.RLock(),
                                   telemetry={'active': False, 'reason': 'paused'})
        self.playback = SimpleNamespace(auto_clicks=True,key_overrides=None,release=Mock())
        self.overlay = TosuOverlay(self.root, self.aim, self.playback)
        self.addCleanup(self.overlay.close)

    def write(self, **changes):
        values = dict(self.overlay.values)
        values.update(changes)
        self.overlay.values_file.write_text(json.dumps(values), encoding='utf-8')
        self.overlay.poll(force=True)

    def test_schema_exposes_every_physics_option_and_visuals(self):
        fields = {item['field'] for item in self.overlay.settings if 'field' in item}
        self.assertEqual(fields, set(asdict(AimConfig())))
        self.assertTrue({'showfov', 'fovcolor', 'fovopacity', 'fovwidth', 'enabled', 'autoclicks'} <= self.overlay.values.keys())

    def test_live_configuration_reaches_the_same_aim_instance(self):
        self.write(strength=.8, radius=140, handgain=.35, requiremotion=False, enabled=False, autoclicks=False)
        self.assertEqual(self.aim.physics.config.strength, .8)

        self.assertEqual(self.aim.physics.config.radius, 140)
        self.assertEqual(self.aim.physics.config.hand_gain, .35)
        self.assertFalse(self.aim.physics.config.require_motion)
        self.assertFalse(self.aim.enabled)
        self.assertFalse(self.playback.auto_clicks)
        self.aim.reset.assert_called_once()

    def test_manual_game_keys_apply_and_can_be_cleared(self):
        self.write(key1='z',key2='x')
        self.assertEqual(self.playback.key_overrides,('z','x'))
        self.playback.release.assert_called_once()
        self.write(key1='',key2='')
        self.assertIsNone(self.playback.key_overrides)

    def test_visual_change_does_not_reset_physics(self):
        self.write(fovcolor='#ff8800', fovopacity=.4)
        self.aim.reset.assert_not_called()

    def test_explicit_debug_flag_survives_saved_overlay_toggle(self):
        self.aim.debug_override = True
        self.write(debug=True)
        self.write(debug=False)
        self.assertTrue(self.aim.debug)

    def test_invalid_settings_keep_last_valid_configuration(self):
        self.write(strength=.75)
        self.write(strength=2)
        self.assertEqual(self.aim.physics.config.strength, .75)
        self.assertIsNotNone(self.overlay.error)

    def test_bad_types_and_nonfinite_values_are_rejected(self):
        for change in ({'enabled': 'false'}, {'strength': True}, {'radius': float('nan')}, {'fovcolor': 'red'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_settings(dict(self.overlay.values, **change), self.overlay.settings)

    def test_reinstall_preserves_saved_preferences(self):
        self.write(strength=.77, showfov=False)
        other = TosuOverlay(self.root, self.aim, self.playback)
        self.addCleanup(other.close)
        self.assertEqual(other.values['strength'], .77)
        self.assertFalse(other.values['showfov'])
        defaults = json.loads((other.folder / 'defaults.json').read_text(encoding='utf-8'))
        self.assertEqual(defaults['strength'], AimConfig().strength)
        self.assertTrue(defaults['showfov'])

    def test_snapshot_is_published_and_close_hides_live_overlay(self):
        self.aim.telemetry.update(active=True, radius=225, cursor=[100, 200])
        self.overlay.publish(force=True)
        state = json.loads((self.overlay.folder / 'state.json').read_text(encoding='utf-8'))
        self.assertEqual(state['radius'], 225)
        self.assertEqual(state['settings']['radius'], 100)
        self.assertTrue(state['active'])
        self.overlay.close()
        stopped = json.loads((self.overlay.folder / 'state.json').read_text(encoding='utf-8'))
        self.assertFalse(stopped['active'])
        self.assertFalse(stopped['connected'])

    def test_partial_file_write_is_not_applied(self):
        self.overlay.values_file.write_text('{', encoding='utf-8')
        self.overlay.poll(force=True)
        self.assertEqual(self.aim.physics.config, AimConfig())
        self.assertIsNotNone(self.overlay.error)

    def test_native_registration_preserves_other_overlays_and_is_idempotent(self):
        original = dict(ingame_profile='default', profiles=[dict(id='default', name='Mine', overlays=[
            dict(id=4, folderName='Other', _enabled=True, width=190, height=100, top=300, left=43, z_index=3)])])
        updated, changed = native_layout(original)
        self.assertTrue(changed)
        self.assertEqual(len(original['profiles'][0]['overlays']), 1)
        self.assertEqual(updated['profiles'][0]['overlays'][0], original['profiles'][0]['overlays'][0])
        entry = updated['profiles'][0]['overlays'][1]
        self.assertEqual((entry['width'], entry['height'], entry['left'], entry['top']), (1920, 1080, 0, 0))
        self.assertEqual(entry['id'], 5)
        again, changed = native_layout(updated)
        self.assertFalse(changed)
        self.assertEqual(again, updated)

    def test_empty_native_configuration_gets_a_default_profile(self):
        updated, changed = native_layout({})
        self.assertTrue(changed)
        self.assertEqual(updated['ingame_profile'], 'default')

    def test_asset_upgrade_preserves_existing_layer_options(self):
        old = dict(id=2, folderName='AimAssist', url='tosu://server/AimAssist/?custom=yes&overlayRev=2',
                   _enabled=False, top=12, left=15, width=1280, height=720, scale=.8)
        original = dict(ingame_profile='default', profiles=[dict(id='default', overlays=[old])])
        updated, changed = native_layout(original)
        self.assertTrue(changed)
        layer = updated['profiles'][0]['overlays'][0]
        self.assertIn('overlayRev=11', layer['url'])
        self.assertIn('custom=yes', layer['url'])
        self.assertEqual({k:v for k,v in layer.items() if k != 'url'}, {k:v for k,v in old.items() if k != 'url'})

    def test_live_cursor_does_not_wait_for_a_new_game_frame(self):
        self.aim.window = (10, 20, 0, 0, 1920, 1080)
        self.aim.user32 = SimpleNamespace(GetForegroundWindow=lambda: 10)
        self.aim.cursor = Mock(side_effect=[(100, 200), (140, 220)])
        self.aim.physics.offset = (5, 2)
        self.aim.telemetry.update(active=True, window=[0, 0, 1920, 1080], cursor=[0, 0], offset=[5, 2])
        self.overlay.publish(force=True)
        first = self.overlay.stream.snapshot()
        second = self.overlay.stream.snapshot()
        self.assertEqual(first['cursor'], [100, 200])
        self.assertEqual(second['cursor'], [140, 220])
        self.assertEqual(second['hand'], [135, 218])
        self.assertEqual(self.aim.telemetry['cursor'], [0, 0])

    def test_stale_game_state_cannot_keep_the_live_fov_active(self):
        self.overlay.stream.update(dict(active=True, source_updated=0, connected=True))
        self.assertFalse(self.overlay.stream.snapshot()['active'])


if __name__ == '__main__':
    unittest.main()
