import unittest
from playback_clock import playback_rate


class ClockTests(unittest.TestCase):
    def test_custom_rates_override_legacy_flags(self):
        for rate in (.5,.9,1,1.2,1.75,2):
            self.assertEqual(playback_rate({'play':{'mods':{'number':64,'rate':rate}}}),rate)

    def test_legacy_frames_still_work(self):
        for number, rate in ((0,1),(64,1.5),(512,1.5),(256,.75)):
            self.assertEqual(playback_rate({'play':{'mods':{'number':number}}}),rate)

    def test_invalid_rates_fail_closed(self):
        for rate in (0,-1,True,None,'1.2',float('nan'),float('inf')):
            with self.subTest(rate=rate), self.assertRaises(ValueError):
                playback_rate({'play':{'mods':{'rate':rate}}})
