"""Existing regressions intentionally leave the endpoint behavior uncovered."""

import unittest

from span import count


class RangeTests(unittest.TestCase):
    def test_reversed_range(self):
        self.assertEqual(count(4, 2), 0)


if __name__ == "__main__":
    unittest.main()
