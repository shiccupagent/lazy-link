#!/usr/bin/env python3
"""Tests for tiles.py: a picture lit brighter on one side still tiles with no light step at the cut
(steel-ground and cloth-herringbone 2026-11-03 showed a wavy band where the old cut met a 25-level step)."""
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter

import tiles


def grain(n=256, seed=0):
    """Fine grey grain (0-1 floats), no light drift of its own."""
    g = gaussian_filter(np.random.default_rng(seed).random((n, n)), 1.0)
    g = (g - g.mean()) / g.std() * 0.04 + 0.5
    return np.repeat(g[..., None], 3, 2)


def lit(a, across=0.0, down=0.0):
    """The picture lit `across` brighter at the right than the left and `down` brighter at the bottom."""
    h, w = a.shape[:2]
    y, x = np.mgrid[0:h, 0:w]
    return np.clip(a + (across * (x / (w - 1) - 0.5) + down * (y / (h - 1) - 0.5))[..., None], 0, 1)


def light_step(t):
    """The biggest jump in broad light along a row or column of the picture repeated 2 x 2 (0-255)."""
    g = gaussian_filter(np.tile(t.mean(2), (2, 2)), 6, mode="wrap")
    return 255 * max(np.abs(np.diff(g.mean(0))).max(), np.abs(np.diff(g.mean(1))).max())


class TileTests(unittest.TestCase):
    def tile(self, a):
        with tempfile.TemporaryDirectory() as d:
            src, out = Path(d) / "in.png", Path(d) / "out.png"
            tiles.save(a, src)
            tiles.seamless(src, out)
            return tiles.load(out)

    def test_a_lit_side_leaves_no_light_step(self):
        for across, down in ((0.3, 0.0), (0.0, 0.1), (0.25, -0.15)):
            with self.subTest(across=across, down=down):
                plain = light_step(self.tile(grain()))
                self.assertLess(light_step(self.tile(lit(grain(), across, down))), plain + 0.5)

    def test_the_old_flatten_alone_left_the_step(self):
        """Guards the test: without detrend and match_tone the same picture shows the step."""
        a = lit(grain(), 0.3)
        f = gaussian_filter(a, (64, 64, 0), mode="reflect")
        left, right = (a - f)[:, :38].mean(), (a - f)[:, -38:].mean()
        self.assertGreater(255 * (right - left), 10)

    def test_detrend_takes_out_a_slope_and_keeps_the_colour(self):
        a = lit(grain(), 0.3, -0.2)
        d = tiles.detrend(a)
        self.assertAlmostEqual(d.mean(), a.mean(), places=6)
        self.assertLess(255 * abs(d[:, :38].mean() - d[:, -38:].mean()), 1.0)
        self.assertLess(255 * abs(d[:38].mean() - d[-38:].mean()), 1.0)

    def test_match_tone_keeps_the_outer_columns(self):
        a = lit(grain(), 0.3)
        end, start = a[:, -38:], a[:, :38]
        e, s = tiles.match_tone(end, start)
        np.testing.assert_allclose(e[:, 0], end[:, 0])
        np.testing.assert_allclose(s[:, -1], start[:, -1])
        self.assertLess(255 * abs((e - s)[:, 19].mean()), 1.0)

    def test_still_seamless_and_the_same_size(self):
        a = lit(grain(), 0.3)
        t = self.tile(a)
        self.assertEqual(t.shape, a.shape)
        self.assertLess(tiles.seam(t), 1.5)


if __name__ == "__main__":
    unittest.main()
