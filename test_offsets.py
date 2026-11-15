import ctypes
import unittest
from unittest.mock import patch

import osu


class OffsetTests(unittest.TestCase):
    def test_successful_read_does_not_scan(self):
        def read(handle, address, output, size, count):
            ctypes.cast(output, ctypes.POINTER(ctypes.c_int32)).contents.value = 1234
            return 1
        with patch.object(osu.ctypes.windll.kernel32, 'ReadProcessMemory', side_effect=read), \
                patch.object(osu, 'scan_osu_memory') as scan:
            self.assertEqual(osu.make_time_reader(1, 100)(), 1234)
            scan.assert_not_called()

    def test_failed_read_automatically_uses_rediscovered_address(self):
        addresses = []
        def read(handle, address, output, size, count):
            addresses.append(address.value)
            if address.value != 200:
                return 0
            ctypes.cast(output, ctypes.POINTER(ctypes.c_int32)).contents.value = 5678
            return 1
        with patch.object(osu.ctypes.windll.kernel32, 'ReadProcessMemory', side_effect=read), \
                patch.object(osu, 'scan_osu_memory', return_value=(200, None)) as scan:
            reader = osu.make_time_reader(1, 100)
            self.assertEqual(reader(), 5678)
            self.assertEqual(reader(), 5678)
            self.assertEqual(addresses, [100, 200, 200])
            scan.assert_called_once_with(1)

    def test_failed_scans_are_throttled_and_retried(self):
        with patch.object(osu.ctypes.windll.kernel32, 'ReadProcessMemory', return_value=0), \
                patch.object(osu, 'scan_osu_memory', return_value=(None, None)) as scan, \
                patch.object(osu.time, 'monotonic', side_effect=[10, 10.5, 12]):
            reader = osu.make_time_reader(1, 100)
            for _ in range(3):
                self.assertIsNone(reader())
            self.assertEqual(scan.call_count, 2)


if __name__ == '__main__':
    unittest.main()
