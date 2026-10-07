"""
shading.py — turn a DEM into the white-canvas silver/charcoal relief texture, and
composite thematic data layers over it.

The look: paper-white lowlands, deep charcoal on shaded mountain faces, pale-blue sea,
optional focus fade outside a region of interest, and data layers as translucent
colour ramps that let the hillshade read through.

    from shading import TerrainCanvas, RAMP_VIOLET
    canvas = TerrainCanvas(dem, cell_m)
    tex = canvas.compose(layers=[(field01, alpha01, RAMP_VIOLET)], focus=inside_mask)

Everything composites one colour channel at a time. That is not premature optimisation:
a full (h, w, 3) float working buffer plus its temporaries runs past a gigabyte at 4K,
and machines with a browser open often do not have that spare.

Requires: numpy, scipy.
"""

from __future__ import annotations

import math

import numpy as np
from scipy.ndimage import gaussian_filter

# Palette measured off Milos Popovic's relief plates rather than eyeballed.
COL_SHADOW = np.array([39, 39, 53], np.float32)      # charcoal slope faces
COL_MIDTONE = np.array([145, 150, 161], np.float32)  # silver mid slopes
COL_LIT = np.array([255, 255, 255], np.float32)      # lit ground / snow
COL_SEA_SHALLOW = np.array([223, 233, 247], np.float32)
COL_SEA_DEEP = np.array([178, 198, 228], np.float32)
COL_BG = np.array([255, 255, 255], np.float32)
COL_BORDER = np.array([46, 46, 64], np.float32)

# Ramps are (position 0-1, RGB). Violet reads well against silver relief and stays
# legible for viewers with red-green colour vision deficiency.
RAMP_VIOLET = [
    (0.00, np.array([207, 169, 255], np.float32)),
    (0.25, np.array([160, 115, 225], np.float32)),
    (0.50, np.array([105, 66, 184], np.float32)),
    (0.75, np.array([72, 39, 152], np.float32)),
    (1.00, np.array([44, 15, 116], np.float32)),
]
RAMP_EMBER = [
    (0.00, np.array([254, 215, 170], np.float32)),
    (0.35, np.array([251, 146, 60], np.float32)),
    (0.70, np.array([194, 65, 12], np.float32)),
    (1.00, np.array([106, 24, 12], np.float32)),
]
RAMP_TEAL = [
    (0.00, np.array([191, 233, 236], np.float32)),
    (0.35, np.array([94, 202, 213], np.float32)),
    (0.70, np.array([13, 132, 152], np.float32)),
    (1.00, np.array([8, 60, 78], np.float32)),
]

CELL_REF_M = 350.0          # shading parameters are quoted at this ground resolution


def hillshade(z, cell, az=315.0, alt=45.0, zf=1.0):
    """Standard ESRI-style hillshade, 0..1."""
    gr, gc = np.gradient(z.astype(np.float32), cell)
    dzdx, dzdy = gc * zf, -gr * zf          # +east, +north
    slope = np.arctan(np.hypot(dzdx, dzdy))
    aspect = np.arctan2(dzdy, -dzdx)
    zen = math.radians(90.0 - alt)
    azm = math.radians(360.0 - az + 90.0)
    hs = (np.cos(zen) * np.cos(slope) +
          np.sin(zen) * np.sin(slope) * np.cos(azm - aspect))
    return np.clip(hs, 0.0, 1.0).astype(np.float32)


def edge_falloff(h, w, north=0.10, west=0.095, east=0.095, south=0.0):
    """0 inside, 1 at the margins, with a smootherstep ramp.

    Used to dissolve the grid into the page and to taper the height field, so the
    working rectangle never reads as a cut slab with cliff walls at its edges.
    """
    def ss(t):
        t = np.clip(t, 0.0, 1.0)
        return t * t * t * (t * (t * 6.0 - 15.0) + 10.0)

    ry = np.linspace(0.0, 1.0, h, dtype=np.float32)
    rx = np.linspace(0.0, 1.0, w, dtype=np.float32)
    e = np.zeros((h, w), np.float32)
    if north:
        e = np.maximum(e, ss(1.0 - ry / north)[:, None])
    if south:
        e = np.maximum(e, ss((ry - (1.0 - south)) / south)[:, None])
    if west:
        e = np.maximum(e, ss(1.0 - rx / west)[None, :])
    if east:
        e = np.maximum(e, ss((rx - (1.0 - east)) / east)[None, :])
    return e


def ramp_lookup(field01, ramp, channel):
    xs = np.array([p for p, _ in ramp], np.float32)
    ys = np.array([c[channel] for _, c in ramp], np.float32)
    return np.interp(field01, xs, ys).astype(np.float32)


class TerrainCanvas:
    """Relief base for a DEM, plus channel-wise compositing of data layers."""

    def __init__(self, dem, cell_m, *, hs_zfactor=4.0, relief_full_m=48.0,
                 sea=True, verbose=True):
        self.dem = dem
        self.cell_m = cell_m
        self.shape = dem.shape
        h, w = dem.shape
        zf = hs_zfactor * (cell_m / CELL_REF_M)   # keeps the look resolution-invariant
        if verbose:
            print(f" [*] hillshade (zf={zf:.2f}) ...")

        hs = np.clip(0.56 * hillshade(dem, cell_m, 315.0, 38.0, zf) +
                     0.22 * hillshade(dem, cell_m, 270.0, 58.0, zf * 0.70) +
                     0.22 * hillshade(dem, cell_m, 15.0, 34.0, zf * 1.55), 0.0, 1.0)

        # Relief gate: plains stay paper-white, mountains take the full tonal range.
        sigma = max(1.5, 6000.0 / cell_m)
        rugged = np.abs(dem - gaussian_filter(dem, sigma))
        relief = np.clip(rugged / relief_full_m, 0.0, 1.0) ** 0.70
        relief = np.clip(gaussian_filter(relief, max(1.0, sigma * 0.3)) * 1.22, 0.0, 1.0)
        del rugged

        shade = np.clip((hs - 0.22) / 0.70, 0.0, 1.0)
        dev = ((1.0 - shade) ** 0.95) * relief
        del shade
        # Ambient-occlusion term: gives whole ranges tonal mass rather than leaving
        # them as bright ground scratched with thin dark ridge lines.
        valley = np.clip((gaussian_filter(dem, sigma * 2.0) - dem) / 170.0, 0.0, 1.0)
        dev = np.clip(dev + 0.40 * valley * relief, 0.0, 1.0)
        del valley

        self.s1 = np.clip(dev / 0.42, 0.0, 1.0)
        self.s2 = np.clip((dev - 0.42) / 0.58, 0.0, 1.0)
        del dev
        self.snow = np.clip(np.clip((dem - 4200.0) / 2600.0, 0.0, 1.0) *
                            np.clip(hs - 0.55, 0.0, 1.0) * 2.0, 0.0, 1.0)
        del hs, relief

        if sea:
            self.depth = np.clip(-dem / 2600.0, 0.0, 1.0)
            self.seam = gaussian_filter((dem <= 0.0).astype(np.float32), 0.8)
        else:
            self.depth = self.seam = None

    def compose(self, layers=(), focus=None, focus_fade=0.30, border=None,
                edge_fade=True, verbose=True):
        """Return a uint8 (h, w, 3) texture.

        layers: iterable of (field01, alpha01, ramp) composited in order.
        focus:  0/1 mask of the region of interest; outside is lifted toward white so
                the subject reads first without hiding its regional context.
        border: 0..1 line mask drawn last so it stays crisp.
        """
        h, w = self.shape
        fade = np.zeros((h, w), np.float32)
        if focus is not None and focus_fade:
            fade += focus_fade * (1.0 - gaussian_filter(focus.astype(np.float32), 1.2))
        if edge_fade:
            fade += edge_falloff(h, w) ** 1.4
        fade = np.clip(fade, 0.0, 1.0)

        if verbose:
            print(" [*] compositing texture channels ...")
        tex = np.empty((h, w, 3), np.uint8)
        for i in range(3):
            c = COL_LIT[i] + (COL_MIDTONE[i] - COL_LIT[i]) * self.s1
            c += (COL_SHADOW[i] - c) * self.s2
            c += (COL_LIT[i] - c) * self.snow
            if self.depth is not None:
                sea_i = (COL_SEA_SHALLOW[i] +
                         (COL_SEA_DEEP[i] - COL_SEA_SHALLOW[i]) * self.depth)
                c += (sea_i - c) * self.seam
                del sea_i
            c += (COL_BG[i] - c) * fade
            for field, alpha, ramp in layers:
                # ramp=None means `field` is already an (h, w, 3) RGB array — used for
                # bivariate schemes and categorical layers, where colour is not a
                # function of one scalar.
                col = field[:, :, i].astype(np.float32) if ramp is None \
                    else ramp_lookup(field, ramp, i)
                c += (col - c) * alpha
                del col
            if border is not None:
                c += (COL_BORDER[i] - c) * border
            tex[:, :, i] = np.clip(c, 0, 255).astype(np.uint8)
            del c
        return tex


def bivariate_rgb(fx, fy, corners, gamma=1.0):
    """Blend four corner colours bilinearly over two normalised fields.

    `corners` is (x0y0, x1y0, x0y1, x1y1) — i.e. (low-low, high-low, low-high,
    high-high) as RGB triples. Returns (h, w, 3) float32.

    Two variables at once is the whole point of a bivariate map, but it only works if
    the four corners are semantically distinct *and* nameable: a reader has to be able
    to look at a colour and say "warm and dry". Pick corners you can put in a sentence.
    """
    c00, c10, c01, c11 = [np.asarray(c, np.float32) for c in corners]
    x = np.clip(fx, 0.0, 1.0)[..., None] ** gamma
    y = np.clip(fy, 0.0, 1.0)[..., None] ** gamma
    top = c00 * (1.0 - x) + c10 * x          # dry edge
    bot = c01 * (1.0 - x) + c11 * x          # wet edge
    return (top * (1.0 - y) + bot * y).astype(np.float32)


def point_density(rows, cols, shape, weights=None, radius_px=6.0, spread=3.1,
                  smooth=0.8, percentile=97.5):
    """Kernel-density field from scattered points, normalised 0..1.

    `radius_px` may be a scalar or per-point array (e.g. scaled by magnitude).
    Normalising at a high percentile rather than the maximum stops one dense cluster
    from flattening everything else to invisibility.
    """
    h, w = shape
    dens = np.zeros((h, w), np.float32)
    rows = np.asarray(rows)
    cols = np.asarray(cols)
    n = len(rows)
    rad = np.full(n, radius_px, np.float32) if np.isscalar(radius_px) else np.asarray(radius_px, np.float32)
    amp = np.ones(n, np.float32) if weights is None else np.asarray(weights, np.float32)

    for k in range(n):
        cy, cx, r = int(rows[k]), int(cols[k]), max(2, int(rad[k]))
        if not (0 <= cy < h and 0 <= cx < w):
            continue
        y0, y1 = max(0, cy - r), min(h, cy + r + 1)
        x0, x1 = max(0, cx - r), min(w, cx + r + 1)
        if y0 >= y1 or x0 >= x1:
            continue
        yy = np.arange(y0, y1, dtype=np.float32) - cy
        xx = np.arange(x0, x1, dtype=np.float32) - cx
        d2 = (yy[:, None] ** 2 + xx[None, :] ** 2) / float(r * r)
        dens[y0:y1, x0:x1] += amp[k] * np.exp(-spread * d2).astype(np.float32)

    if smooth:
        dens = gaussian_filter(dens, smooth)
    hot = dens[dens > 0]
    dmax = float(np.percentile(hot, percentile)) if hot.size else 1.0
    return np.clip(dens / max(dmax, 1e-6), 0.0, 1.0)
