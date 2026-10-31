#!/usr/bin/env python3
"""Tests for imagegen/bumpmaps.py. Synthetic data only, no PC. Run from the repo root:
  python3 -m unittest imagegen.test_bumpmaps"""
import sys
import unittest
from pathlib import Path

import numpy as np
from scipy.ndimage import gaussian_filter

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bumpmaps  # noqa: E402

N = 256


def surface(broad=1.0, fine=1.0):
    """A wrapping height field: one broad wave across the tile plus fine bumps 8 px apart, and its OpenGL normal map
    (slopes from the exact derivatives, so nothing but the filter under test changes them)."""
    y, x = np.mgrid[0:N, 0:N].astype(float)
    k1, k2 = 2 * np.pi / N, 2 * np.pi / 8
    h = broad * 20 * np.sin(k1 * x) * np.cos(k1 * y) + fine * 0.6 * np.sin(k2 * x) * np.sin(k2 * y)
    dx = broad * 20 * k1 * np.cos(k1 * x) * np.cos(k1 * y) + fine * 0.6 * k2 * np.cos(k2 * x) * np.sin(k2 * y)
    dy = -broad * 20 * k1 * np.sin(k1 * x) * np.sin(k1 * y) + fine * 0.6 * k2 * np.sin(k2 * x) * np.cos(k2 * y)
    return h, bumpmaps.unit(np.dstack([-dx, dy, np.ones_like(h)]))   # rows grow downward: +Y up flips dy


def slopes(n):
    return n[..., 0] / n[..., 2], n[..., 1] / n[..., 2]


class HighpassTests(unittest.TestCase):
    def test_takes_out_the_broad_wave_and_keeps_the_fine_bumps(self):
        _, both = surface()
        _, fine_only = surface(broad=0.0)
        _, broad_only = surface(fine=0.0)
        got = bumpmaps.highpass_normal(both, 16, "wrap")
        for a, f, b in zip(slopes(got), slopes(fine_only), slopes(broad_only)):
            # what is left of the 256 px wave (diagonal, so |k| = sqrt(2) 2 pi / 256): 1 - exp(-(16 |k|)^2 / 2) = 14%
            self.assertLess(np.abs(a - f).mean(), 0.16 * np.abs(b).mean())
            self.assertGreater(np.corrcoef(a.ravel(), f.ravel())[0, 1], 0.97)

    def test_a_flat_map_stays_flat(self):
        flat = np.dstack([np.zeros((32, 32)), np.zeros((32, 32)), np.ones((32, 32))])
        np.testing.assert_allclose(bumpmaps.highpass_normal(flat, 8, "wrap"), flat, atol=1e-12)

    def test_wrap_mode_keeps_a_tile_wrapping(self):
        _, n = surface()
        rolled = np.roll(n, (N // 2, N // 3), axis=(0, 1))
        np.testing.assert_allclose(bumpmaps.highpass_normal(rolled, 16, "wrap"),
                                   np.roll(bumpmaps.highpass_normal(n, 16, "wrap"), (N // 2, N // 3), axis=(0, 1)),
                                   atol=1e-9)

    def test_the_height_is_the_high_passed_surface(self):
        # slopes are the height's derivatives, so filtering them filters the height: exact for a wrapping tile
        h_true, both = surface()
        want = h_true - gaussian_filter(h_true, 16, mode="wrap")
        got = bumpmaps.height_from_normal(bumpmaps.highpass_normal(both, 16, "wrap"), True)
        self.assertGreater(np.corrcoef(got.ravel(), want.ravel())[0, 1], 0.995)
        raw = bumpmaps.height_from_normal(both, True)
        self.assertGreater(raw.std(), 5 * got.std())                         # without it the broad wave's relief stays

    def test_unit_length(self):
        _, n = surface()
        got = bumpmaps.highpass_normal(n, 12, "nearest")
        np.testing.assert_allclose(np.linalg.norm(got, axis=2), 1.0, atol=1e-9)


if __name__ == "__main__":
    unittest.main()
