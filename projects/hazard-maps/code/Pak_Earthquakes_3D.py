"""
Pak_Earthquakes_3D.py
=====================
Oblique 3D shaded-relief earthquake map of Pakistan, in the style of Milos Popovic's
"Earthquakes ... with magnitude X or higher" series (white canvas, near-white silver
relief with deep charcoal shadow faces, vivid violet epicentre clusters, soft
periwinkle cast shadow, classic serif title block).

Pipeline
--------
1. Elevation + bathymetry : AWS Terrain Tiles (Mapzen "terrarium" encoding, z=9),
                            mosaicked and resampled to an equal-ground-pixel lon/lat grid.
                            Bathymetry is what lets the Arabian Sea render at the bottom.
2. Seismicity             : USGS FDSNWS / ComCat event catalogue, M >= 2.5, 1900 -> present.
3. Boundary               : UN OCHA / HDX Pakistan ADM0 (falls back to the OSM polygon).
4. Texture synthesis      : multi-directional hillshade -> relief-weighted silver/white ramp,
                            pale-blue sea, Pakistan focus mask, violet epicentre density.
5. Oblique 3D render      : purpose-built painter's-algorithm terrain rasteriser
                            (perspective camera, real height displacement + occlusion).
6. Composition            : directional cast shadow, serif typography, 4K export.

Outputs
-------
outputs/pakistan_earthquakes_3d.png
outputs/pakistan_earthquakes_3d.jpg

Run
---
    py Pak_Earthquakes_3D.py              # full 4K render
    py Pak_Earthquakes_3D.py --preview    # fast low-res look-development pass

Note on forge3d
---------------
forge3d 1.35 is installed and its `MapScene` recipe renders a *flat, top-down* map
(its OrbitCamera elevation is not honoured by that code path), while the
`hybrid_render_terrain_reference` path tracer fails on this machine's integrated
Adreno GPU ("ReSTIR reuse chain produced no valid reservoirs"). Neither can produce
the oblique, height-displaced geometry this style depends on, so the 3D rasteriser
below is used instead. Set USE_FORGE3D_PROBE = True to re-test both on other hardware.
"""

from __future__ import annotations

import argparse
import gc
import io
import math
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import requests
from PIL import Image, ImageDraw, ImageFont
from scipy.ndimage import gaussian_filter, map_coordinates

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
CACHE_DIR = os.path.join(DATA_DIR, "eq3d_cache")
TILE_DIR = os.path.join(CACHE_DIR, "terrarium_z9")
OUT_DIR = os.path.join(BASE_DIR, "outputs")

# Frame of the map. Pakistan spans 60.87-77.12E / 23.43-37.09N; the south edge is
# pushed well past the coast so the Arabian Sea reads as a band at the bottom.
LON_MIN, LON_MAX = 59.6, 78.4
LAT_MIN, LAT_MAX = 21.2, 37.4

TILE_ZOOM = 9                 # 512 tiles / 360 deg -> ~265 m per pixel at this latitude
SRC_W = 4200                  # working grid width (equal ground pixels)

MAG_MIN = 2.5
YEAR_START, YEAR_END = 1900, 2026

# Shaking map: USGS ShakeMap coverage is a modern product, so this layer uses the
# last 25 years, where the catalogue and the ShakeMap Atlas are both homogeneous.
SHAKE_YEAR_START = 2000
SHAKE_MAG_MIN = 4.5
MMI_MIN, MMI_MAX = 4.0, 8.5   # MMI IV (light) .. VIII+ (severe); 98%% of PK exceeds IV
SHAKE_OPACITY = 0.62          # keep the wash translucent so relief still reads
CAUS_MAG_MIN, CAUS_MAG_MAX = 4.5, 7.8   # causative-magnitude ramp bounds

# Render
OUT_W, OUT_H = 3840, 2160     # 4K deliverable
SUPERSAMPLE = 2               # render at 2x then Lanczos down

# Camera (perspective, looking due north, tilted down)
CAM_PITCH_DEG = 50.0          # downward tilt; lower = more dramatic / more occlusion
CAM_DIST_FACTOR = 2.15        # eye distance as a multiple of the map's N-S extent
CAM_FOV_DEG = 36.0
CAM_ZOOM = 1.50               # framing: >1 lets the slab overfill the frame edges
CAM_PAN_X = 0.075             # fraction of frame width; + moves the map right
CAM_PAN_Y = 0.045             # fraction of frame height; + moves the map down
# Geometric extrusion is deliberately modest — as in the reference plate, the drama
# comes from the hillshade, not from the height displacement.
VERT_EXAG = 13.0
# Height field is smoothed before displacement so isolated Karakoram pixels do not
# project as needle spikes; the texture keeps the full-resolution detail.
GEOM_SMOOTH_M = 2600.0

# Shading, expressed at a reference ground resolution so preview and full-res match.
CELL_REF_M = 350.0
HS_ZFACTOR = 4.0              # hillshade vertical exaggeration at CELL_REF_M
RELIEF_FULL_M = 48.0          # local relief (m) at which contrast is fully applied
FOCUS_FADE = 0.30             # how far neighbouring countries lift toward white
SKIRT_DARK = 0.45             # shading applied down a projected cliff face

# Palette — measured directly off the reference plate (milosgis.com), see notes below.
COL_SHADOW = np.array([39, 39, 53], np.float32)       # deep charcoal slope faces
COL_MIDTONE = np.array([145, 150, 161], np.float32)   # silver mid slopes
COL_LIT = np.array([255, 255, 255], np.float32)       # lit ground / snow
COL_SEA_SHALLOW = np.array([223, 233, 247], np.float32)
COL_SEA_DEEP = np.array([178, 198, 228], np.float32)
COL_BG = np.array([255, 255, 255], np.float32)
COL_CAST_SHADOW = np.array([186, 201, 228], np.float32)  # periwinkle ground shadow
COL_TITLE = (17, 17, 20)
COL_ACCENT = (74, 80, 195)                             # violet-blue "PAKISTAN"
COL_CREDIT = (78, 78, 88)

# Violet epicentre ramp, sparse -> dense (percentiles of the reference's purple pixels)
EQ_RAMP = [
    (0.00, np.array([207, 169, 255], np.float32)),
    (0.25, np.array([160, 115, 225], np.float32)),
    (0.50, np.array([105, 66, 184], np.float32)),
    (0.75, np.array([72, 39, 152], np.float32)),
    (1.00, np.array([44, 15, 116], np.float32)),
]

USE_FORGE3D_PROBE = False     # re-test forge3d's 3D paths on capable hardware

os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(TILE_DIR, exist_ok=True)
os.makedirs(OUT_DIR, exist_ok=True)


def log(msg: str) -> None:
    print(msg, flush=True)


# ---------------------------------------------------------------------------
# 1. Elevation + bathymetry from AWS Terrain Tiles
# ---------------------------------------------------------------------------

def _deg2tile(lon: float, lat: float, z: int) -> tuple[float, float]:
    n = 2.0 ** z
    x = (lon + 180.0) / 360.0 * n
    lat_r = math.radians(lat)
    y = (1.0 - math.log(math.tan(lat_r) + 1.0 / math.cos(lat_r)) / math.pi) / 2.0 * n
    return x, y


def _fetch_tile(args) -> tuple[int, int, np.ndarray | None]:
    z, x, y = args
    path = os.path.join(TILE_DIR, f"{z}_{x}_{y}.png")
    if os.path.exists(path):
        try:
            return x, y, np.asarray(Image.open(path).convert("RGB"))
        except Exception:
            os.remove(path)
    url = f"https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
    for attempt in range(4):
        try:
            r = requests.get(url, timeout=30)
            if r.status_code == 404:
                return x, y, None
            r.raise_for_status()
            with open(path, "wb") as fh:
                fh.write(r.content)
            return x, y, np.asarray(Image.open(io.BytesIO(r.content)).convert("RGB"))
        except Exception:
            time.sleep(0.6 * (attempt + 1))
    return x, y, None


def build_dem(src_w: int) -> tuple[np.ndarray, float]:
    """Mosaic terrarium tiles and resample to an equal-ground-pixel lon/lat grid.

    Returns (elevation grid in metres, ground cell size in metres).
    """
    lat_mid = 0.5 * (LAT_MIN + LAT_MAX)
    # Equal ground pixels: 1 deg lon = 111320*cos(lat) m, 1 deg lat = 110570 m.
    ground_w = (LON_MAX - LON_MIN) * 111320.0 * math.cos(math.radians(lat_mid))
    ground_h = (LAT_MAX - LAT_MIN) * 110570.0
    src_h = int(round(src_w * ground_h / ground_w))
    cell_m = ground_w / src_w

    cache = os.path.join(CACHE_DIR, f"dem_{src_w}x{src_h}_z{TILE_ZOOM}.npy")
    if os.path.exists(cache):
        dem = np.load(cache)
        log(f" [+] DEM cache hit: {dem.shape[1]}x{dem.shape[0]} @ {cell_m:,.0f} m/px")
        return dem, cell_m

    x0f, y1f = _deg2tile(LON_MIN, LAT_MIN, TILE_ZOOM)
    x1f, y0f = _deg2tile(LON_MAX, LAT_MAX, TILE_ZOOM)
    x0, x1 = int(math.floor(x0f)), int(math.floor(x1f))
    y0, y1 = int(math.floor(y0f)), int(math.floor(y1f))
    jobs = [(TILE_ZOOM, x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)]
    log(f" [*] Fetching {len(jobs)} terrarium tiles (z={TILE_ZOOM}) ...")

    nx, ny = (x1 - x0 + 1), (y1 - y0 + 1)
    mosaic = np.zeros((ny * 256, nx * 256), np.float32)
    done = 0
    with ThreadPoolExecutor(max_workers=16) as pool:
        for x, y, rgb in pool.map(_fetch_tile, jobs):
            done += 1
            if done % 100 == 0:
                log(f"     {done}/{len(jobs)} tiles")
            if rgb is None:
                continue
            a = rgb.astype(np.float32)
            elev = (a[:, :, 0] * 256.0 + a[:, :, 1] + a[:, :, 2] / 256.0) - 32768.0
            r0 = (y - y0) * 256
            c0 = (x - x0) * 256
            mosaic[r0:r0 + 256, c0:c0 + 256] = elev
    log(f" [+] Mosaic assembled: {mosaic.shape[1]}x{mosaic.shape[0]} px")

    # Resample the Web-Mercator mosaic onto the regular lon/lat working grid.
    lons = np.linspace(LON_MIN, LON_MAX, src_w, dtype=np.float64)
    lats = np.linspace(LAT_MAX, LAT_MIN, src_h, dtype=np.float64)  # row 0 = north
    n = 2.0 ** TILE_ZOOM
    tx = (lons + 180.0) / 360.0 * n
    lat_r = np.radians(lats)
    ty = (1.0 - np.log(np.tan(lat_r) + 1.0 / np.cos(lat_r)) / np.pi) / 2.0 * n
    px = (tx - x0) * 256.0
    py = (ty - y0) * 256.0
    cc, rr = np.meshgrid(px, py)
    dem = map_coordinates(mosaic, [rr, cc], order=1, mode="nearest").astype(np.float32)
    del mosaic, cc, rr

    np.save(cache, dem)
    log(f" [+] DEM built: {dem.shape[1]}x{dem.shape[0]} @ {cell_m:,.0f} m/px "
        f"(min {dem.min():,.0f} m, max {dem.max():,.0f} m)")
    return dem, cell_m


# ---------------------------------------------------------------------------
# 2. USGS earthquake catalogue
# ---------------------------------------------------------------------------

def fetch_quakes() -> np.ndarray:
    """Return an (N, 3) array of [lon, lat, magnitude]."""
    cache = os.path.join(CACHE_DIR, f"quakes_m{MAG_MIN}_{YEAR_START}_{YEAR_END}.npy")
    if os.path.exists(cache):
        q = np.load(cache)
        log(f" [+] Quake cache hit: {len(q):,} events (M>={MAG_MIN})")
        return q

    url = (
        "https://earthquake.usgs.gov/fdsnws/event/1/query?format=csv"
        f"&starttime={YEAR_START}-01-01&endtime={YEAR_END}-01-01"
        f"&minmagnitude={MAG_MIN}"
        f"&minlatitude={LAT_MIN - 0.6}&maxlatitude={LAT_MAX + 0.6}"
        f"&minlongitude={LON_MIN - 0.6}&maxlongitude={LON_MAX + 0.6}"
        "&orderby=time"
    )
    log(" [*] Querying USGS ComCat ...")
    r = requests.get(url, timeout=180)
    r.raise_for_status()

    import csv
    rows = list(csv.DictReader(io.StringIO(r.text)))
    recs = []
    for row in rows:
        try:
            lon = float(row["longitude"])
            lat = float(row["latitude"])
            mag = float(row["mag"])
        except (TypeError, ValueError, KeyError):
            continue
        recs.append((lon, lat, mag))
    q = np.array(recs, np.float32)
    np.save(cache, q)
    log(f" [+] Retrieved {len(q):,} events, M {q[:, 2].min():.1f}-{q[:, 2].max():.1f}")
    return q


# ---------------------------------------------------------------------------
# 3. Pakistan boundary -> raster masks
# ---------------------------------------------------------------------------

def boundary_masks(shape: tuple[int, int]) -> tuple[np.ndarray, np.ndarray]:
    """Return (inside_mask float32 0/1, border_line float32 0..1)."""
    import geopandas as gpd
    from rasterio.features import rasterize
    from rasterio.transform import from_bounds

    h, w = shape
    cache = os.path.join(CACHE_DIR, f"pakmask_{w}x{h}.npz")
    if os.path.exists(cache):
        z = np.load(cache)
        log(" [+] Boundary mask cache hit")
        return z["inside"], z["line"]

    candidates = [
        os.path.join(DATA_DIR, "admin_boundaries", "pak_admin0.shp"),
        os.path.join(DATA_DIR, "pakistan_boundary.geojson"),
    ]
    src = next((p for p in candidates if os.path.exists(p)), None)
    if src is None:
        raise FileNotFoundError("No Pakistan boundary dataset found in data/")
    gdf = gpd.read_file(src).to_crs("EPSG:4326")
    log(f" [+] Boundary source: {os.path.basename(src)}")

    transform = from_bounds(LON_MIN, LAT_MIN, LON_MAX, LAT_MAX, w, h)
    geoms = [g for g in gdf.geometry if g is not None and not g.is_empty]

    inside = rasterize([(g, 1) for g in geoms], out_shape=(h, w), transform=transform,
                       fill=0, dtype=np.uint8).astype(np.float32)

    # Border line width scaled to the working grid.
    lw = max(0.010, 0.010 * (5200.0 / w))
    line_geoms = [(g.boundary.buffer(lw), 1) for g in geoms]
    line = rasterize(line_geoms, out_shape=(h, w), transform=transform,
                     fill=0, dtype=np.uint8).astype(np.float32)
    line = np.clip(gaussian_filter(line, 0.7), 0.0, 1.0)

    np.savez_compressed(cache, inside=inside, line=line)
    return inside, line


# ---------------------------------------------------------------------------
# 4. Texture synthesis
# ---------------------------------------------------------------------------

def edge_falloff(h: int, w: int) -> np.ndarray:
    """0 in the interior, 1 at the north/east/west margins of the grid.

    Used to dissolve the texture into the page *and* to taper the height field, so
    the working grid never reads as a cut rectangular slab with cliff walls.
    """
    def smootherstep(t):
        t = np.clip(t, 0.0, 1.0)
        return t * t * t * (t * (t * 6.0 - 15.0) + 10.0)

    ry = np.linspace(0.0, 1.0, h, dtype=np.float32)
    rx = np.linspace(0.0, 1.0, w, dtype=np.float32)
    e = smootherstep(1.0 - ry / 0.10)[:, None]                       # north
    e = np.maximum(e, smootherstep(1.0 - rx / 0.095)[None, :])       # west
    e = np.maximum(e, smootherstep((rx - 0.905) / 0.095)[None, :])   # east
    return e.astype(np.float32)


def hillshade(z: np.ndarray, cell: float, az: float, alt: float, zf: float) -> np.ndarray:
    gr, gc = np.gradient(z.astype(np.float32), cell)
    dzdx = gc * zf            # +east
    dzdy = -gr * zf           # +north
    slope = np.arctan(np.hypot(dzdx, dzdy))
    aspect = np.arctan2(dzdy, -dzdx)
    zen = math.radians(90.0 - alt)
    azm = math.radians(360.0 - az + 90.0)
    hs = (np.cos(zen) * np.cos(slope) +
          np.sin(zen) * np.sin(slope) * np.cos(azm - aspect))
    return np.clip(hs, 0.0, 1.0).astype(np.float32)


def build_texture(dem: np.ndarray, cell_m: float, layer_fn) -> np.ndarray:
    """Shade the terrain and composite a thematic layer over it.

    `layer_fn(h, w, inside_soft) -> (field, alpha, ramp)` supplies the data layer:
    `field` is normalised 0..1 and drives `ramp`, `alpha` is its opacity. This keeps
    the relief/sea/border treatment identical between the epicentre and shaking maps.
    """
    h, w = dem.shape
    land = dem > 0.0

    # -- relief shading ----------------------------------------------------
    # zfactor is scaled by cell size so the look is identical at any resolution.
    zf = HS_ZFACTOR * (cell_m / CELL_REF_M)
    log(f" [*] Computing multi-directional hillshade (zf={zf:.2f}) ...")
    hs_main = hillshade(dem, cell_m, az=315.0, alt=38.0, zf=zf)
    hs_fill = hillshade(dem, cell_m, az=270.0, alt=58.0, zf=zf * 0.70)
    hs_det = hillshade(dem, cell_m, az=15.0, alt=34.0, zf=zf * 1.55)
    hs = np.clip(0.56 * hs_main + 0.22 * hs_fill + 0.22 * hs_det, 0.0, 1.0)

    # Relief weight: plains stay near-white, mountains take the full contrast range.
    sigma_px = max(1.5, 6000.0 / cell_m)          # ~6 km smoothing window
    rugged = np.abs(dem - gaussian_filter(dem, sigma_px))
    relief = np.clip(rugged / RELIEF_FULL_M, 0.0, 1.0) ** 0.70
    relief = np.clip(gaussian_filter(relief, max(1.0, sigma_px * 0.3)) * 1.22, 0.0, 1.0)

    # Contrast-stretch the hillshade about a bright pivot, then gate it by relief
    # so flat ground stays paper-white and only real terrain takes ink.
    shade = np.clip((hs - 0.22) / 0.70, 0.0, 1.0)
    dev = ((1.0 - shade) ** 0.95) * relief

    # Ambient-occlusion style valley darkening: gives whole ranges tonal mass
    # instead of leaving them as bright ground with thin dark ridge lines.
    valley = np.clip((gaussian_filter(dem, sigma_px * 2.0) - dem) / 170.0, 0.0, 1.0)
    dev = np.clip(dev + 0.40 * valley * relief, 0.0, 1.0)

    # Two-segment tone ramp weights: white -> silver -> charcoal.
    s1 = np.clip(dev / 0.42, 0.0, 1.0)
    s2 = np.clip((dev - 0.42) / 0.58, 0.0, 1.0)
    del dev

    # Snow / high-altitude brightening on lit faces of the great ranges.
    snow = np.clip(np.clip((dem - 4200.0) / 2600.0, 0.0, 1.0) *
                   np.clip(hs - 0.55, 0.0, 1.0) * 2.0, 0.0, 1.0)
    del hs, relief

    # -- sea ---------------------------------------------------------------
    depth = np.clip(-dem / 2600.0, 0.0, 1.0)
    # feather the coastline by a pixel or two so it does not alias
    sm = gaussian_filter((~land).astype(np.float32), 0.8)
    del land

    # -- Pakistan focus + border ------------------------------------------
    log(" [*] Applying Pakistan focus mask and border ...")
    inside, line = boundary_masks(dem.shape)
    inside_soft = gaussian_filter(inside, 1.2)
    del inside
    # Lift neighbours toward white, and dissolve the far (north) edge and the
    # flanks into the page so the grid never reads as a cut rectangular slab.
    fade = np.clip(FOCUS_FADE * (1.0 - inside_soft) + edge_falloff(h, w) ** 1.4, 0.0, 1.0)

    # -- thematic layer ----------------------------------------------------
    dn, eq_a, ramp = layer_fn(h, w, inside_soft)
    # border line composited last so it stays crisp
    bl = np.clip(line * (0.42 + 0.30 * inside_soft), 0.0, 1.0)
    del line, inside_soft

    # -- composite, one channel at a time ---------------------------------
    # Done channel-wise on purpose: a full (h, w, 3) float32 working buffer plus
    # its temporaries runs to well over a gigabyte at 4K, which this machine does
    # not have spare. This keeps the peak to a single (h, w) float32 scratch.
    log(" [*] Compositing texture channels ...")
    ramp_x = np.array([p for p, _ in ramp], np.float32)
    border_col = (46.0, 46.0, 64.0)
    tex = np.empty((h, w, 3), np.uint8)
    for i in range(3):
        c = COL_LIT[i] + (COL_MIDTONE[i] - COL_LIT[i]) * s1     # white -> silver
        c += (COL_SHADOW[i] - c) * s2                           # -> charcoal
        c += (COL_LIT[i] - c) * snow                            # snow caps
        sea_i = COL_SEA_SHALLOW[i] + (COL_SEA_DEEP[i] - COL_SEA_SHALLOW[i]) * depth
        c += (sea_i - c) * sm                                   # Arabian Sea
        del sea_i
        c += (COL_BG[i] - c) * fade                             # focus + edge fade
        ramp_y = np.array([col[i] for _, col in ramp], np.float32)
        eq_i = np.interp(dn, ramp_x, ramp_y).astype(np.float32)
        c += (eq_i - c) * eq_a                                  # violet epicentres
        del eq_i
        c += (border_col[i] - c) * bl                           # national border
        tex[:, :, i] = np.clip(c, 0, 255).astype(np.uint8)
        del c

    return tex


# ---------------------------------------------------------------------------
# 4b. Thematic layers: epicentres (where quakes START) vs shaking (what is FELT)
# ---------------------------------------------------------------------------

def epicentre_layer(quakes: np.ndarray):
    """Kernel-density of epicentres — i.e. where ruptures *originated*."""
    def fn(h, w, inside_soft):
        log(f" [*] Splatting {len(quakes):,} epicentres ...")
        dens = np.zeros((h, w), np.float32)
        lon_span, lat_span = (LON_MAX - LON_MIN), (LAT_MAX - LAT_MIN)
        scale = w / 5200.0
        for lon, lat, mag in quakes:
            if not (LON_MIN <= lon <= LON_MAX and LAT_MIN <= lat <= LAT_MAX):
                continue
            cx = int((lon - LON_MIN) / lon_span * (w - 1))
            cy = int((LAT_MAX - lat) / lat_span * (h - 1))
            rad = max(2, int((max(mag, 2.5) - 1.6) ** 1.42 * 3.1 * scale))
            amp = 0.58 + 0.24 * (max(mag, 2.5) - 2.5) ** 1.28
            x0, x1 = max(0, cx - rad), min(w, cx + rad + 1)
            y0, y1 = max(0, cy - rad), min(h, cy + rad + 1)
            if x0 >= x1 or y0 >= y1:
                continue
            yy = np.arange(y0, y1, dtype=np.float32) - cy
            xx = np.arange(x0, x1, dtype=np.float32) - cx
            d2 = (yy[:, None] ** 2 + xx[None, :] ** 2) / float(rad * rad)
            dens[y0:y1, x0:x1] += amp * np.exp(-3.1 * d2).astype(np.float32)

        dens = gaussian_filter(dens, max(0.6, 0.8 * scale))
        dmax = float(np.percentile(dens[dens > 0], 97.5)) if np.any(dens > 0) else 1.0
        dn = np.clip(dens / max(dmax, 1e-6), 0.0, 1.0)
        del dens
        a = np.clip(dn * 3.1, 0.0, 1.0) ** 0.72
        a *= (0.62 + 0.38 * inside_soft) * 0.97
        return dn, a, EQ_RAMP
    return fn


def shaking_layer(mmi: np.ndarray):
    """Maximum modelled Modified Mercalli Intensity — i.e. what the ground *felt*."""
    def fn(h, w, inside_soft):
        dn = np.clip((mmi - MMI_MIN) / (MMI_MAX - MMI_MIN), 0.0, 1.0)
        a = np.clip((mmi - (MMI_MIN - 0.1)) / 1.6, 0.0, 1.0) ** 0.90
        # Clip to national territory (feathered). Individual ShakeMap grids are
        # finite rectangles, so their union has hard steps outside the dense-
        # coverage core; inside Pakistan coverage is 100%, outside it is not.
        edge = gaussian_filter(inside_soft, max(2.0, w / 300.0))
        a *= np.clip(edge * 1.15, 0.0, 1.0)
        # Held well below 1 so the hillshade still reads through the wash.
        a *= SHAKE_OPACITY
        return dn, a, EQ_RAMP
    return fn


def _parse_shakemap_grid(raw: bytes):
    """Return (mmi 2-D array, lon_min, lon_max, lat_min, lat_max) from a grid.xml."""
    import gzip
    import re
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    txt = raw.decode("utf-8", "replace")
    m = re.search(r"<grid_specification([^>]*?)/?>", txt)
    if not m:
        return None
    g = {k: float(v) for k, v in re.findall(r'(\w+)="([-\d.eE+]+)"', m.group(1))}
    names = [n for _, n in re.findall(r'<grid_field index="(\d+)" name="([^"]+)"', txt)]
    if "MMI" not in names or "<grid_data>" not in txt:
        return None
    nlon, nlat = int(g["nlon"]), int(g["nlat"])
    body = txt.split("<grid_data>")[1].split("</grid_data>")[0].strip()
    arr = np.loadtxt(body.splitlines(), dtype=np.float32)
    if arr.shape[0] != nlon * nlat:
        return None
    # Rows are ordered latitude-descending, longitude-ascending.
    mmi = arr[:, names.index("MMI")].reshape(nlat, nlon)
    return mmi, g["lon_min"], g["lon_max"], g["lat_min"], g["lat_max"]


def causative_layer(mag: np.ndarray, mmi: np.ndarray):
    """Magnitude of the earthquake that delivered the strongest shaking at each cell.

    Magnitude is a property of the *source*, not of a place, so an MMI field cannot
    be "converted" to magnitude pointwise. What *is* well defined is attribution:
    for every cell, which event produced its maximum intensity, and what was that
    event's magnitude. Cells that have never felt at least MMI IV stay uncoloured.
    """
    def fn(h, w, inside_soft):
        dn = np.clip((mag - CAUS_MAG_MIN) / (CAUS_MAG_MAX - CAUS_MAG_MIN), 0.0, 1.0)
        a = np.clip((mmi - 3.9) / 0.6, 0.0, 1.0)          # only where actually felt
        edge = gaussian_filter(inside_soft, max(2.0, w / 300.0))
        a = a * np.clip(edge * 1.15, 0.0, 1.0) * SHAKE_OPACITY
        return dn, a, EQ_RAMP
    return fn


def shakemap_composite(h: int, w: int):
    """Cell-wise MAXIMUM ShakeMap MMI over all catalogued events in the window.

    This is the layer that answers "where has the ground actually been shaken?",
    as opposed to the epicentre map's "where did ruptures start?". Intensity decays
    with distance from the source, so populated lowlands hundreds of km from any
    epicentre still register here.

    Returns (max_mmi, causative_magnitude, n_grids), where causative_magnitude
    records the magnitude of the event responsible for each cell's maximum.
    """
    tag = f"{SHAKE_YEAR_START}_{SHAKE_MAG_MIN}"
    cache = os.path.join(CACHE_DIR, f"maxmmi_{w}x{h}_{tag}.npz")
    if os.path.exists(cache):
        z = np.load(cache)
        if "mag" in z:
            log(f" [+] Max-MMI cache hit ({w}x{h}, {int(z['used'])} grids)")
            return z["mmi"], z["mag"], int(z["used"])

    # Any cached composite at another size can simply be resampled: ShakeMap grids
    # are ~0.033 deg (~3.3 km), coarser than any working grid used here, so this
    # costs no real detail and avoids re-downloading ~3 GB of grid.xml.
    import glob
    alt = [p for p in sorted(glob.glob(os.path.join(CACHE_DIR, f"maxmmi_*_{tag}.npz")))
           if "mag" in np.load(p)]
    if alt:
        z = np.load(alt[-1])
        rr = np.linspace(0, z["mmi"].shape[0] - 1, h, dtype=np.float32)
        cc = np.linspace(0, z["mmi"].shape[1] - 1, w, dtype=np.float32)
        g0, g1 = np.meshgrid(cc, rr)
        out = map_coordinates(z["mmi"], [g1, g0], order=1, mode="nearest").astype(np.float32)
        # nearest-neighbour for magnitude: it is a categorical attribution, not a field
        mg = map_coordinates(z["mag"], [g1, g0], order=0, mode="nearest").astype(np.float32)
        log(f" [+] Max-MMI resampled from {z['mmi'].shape[1]}x{z['mmi'].shape[0]} "
            f"-> {w}x{h} ({int(z['used'])} grids)")
        np.savez_compressed(cache, mmi=out, mag=mg, used=int(z["used"]))
        return out, mg, int(z["used"])

    listing = (
        "https://earthquake.usgs.gov/fdsnws/event/1/query?format=geojson"
        f"&starttime={SHAKE_YEAR_START}-01-01&endtime={YEAR_END}-01-01"
        f"&minmagnitude={SHAKE_MAG_MIN}&producttype=shakemap"
        f"&minlatitude={LAT_MIN - 3}&maxlatitude={LAT_MAX + 3}"
        f"&minlongitude={LON_MIN - 3}&maxlongitude={LON_MAX + 3}"
    )
    log(" [*] Listing events with USGS ShakeMap products ...")
    feats = requests.get(listing, timeout=180).json()["features"]
    log(f" [+] {len(feats)} ShakeMap events since {SHAKE_YEAR_START}")

    out = np.zeros((h, w), np.float32)
    srcmag = np.zeros((h, w), np.float32)
    lons = np.linspace(LON_MIN, LON_MAX, w, dtype=np.float32)
    lats = np.linspace(LAT_MAX, LAT_MIN, h, dtype=np.float32)   # row 0 = north

    def grab(f):
        eid = f["id"]
        emag = float(f["properties"].get("mag") or 0.0)
        try:
            d = requests.get(
                "https://earthquake.usgs.gov/fdsnws/event/1/query?"
                f"eventid={eid}&format=geojson", timeout=90).json()
            prods = d["properties"]["products"].get("shakemap") or []
            if not prods:
                return eid, emag, None
            cont = prods[0]["contents"]
            key = next((k for k in ("download/grid.xml", "download/grid.xml.zip")
                        if k in cont), None)
            if key is None:
                return eid, emag, None
            return eid, emag, requests.get(cont[key]["url"], timeout=240).content
        except Exception:
            return eid, emag, None

    done = used = 0
    with ThreadPoolExecutor(max_workers=6) as pool:
        for eid, emag, raw in pool.map(grab, feats):
            done += 1
            if raw is None:
                continue
            try:
                parsed = _parse_shakemap_grid(raw)
            except Exception:
                parsed = None
            if parsed is None:
                continue
            mmi, lo0, lo1, la0, la1 = parsed
            gh, gw = mmi.shape
            # Sample this event's grid onto the working grid (bilinear), then max.
            cols = (lons - lo0) / max(lo1 - lo0, 1e-9) * (gw - 1)
            rows = (la1 - lats) / max(la1 - la0, 1e-9) * (gh - 1)
            cvalid = (cols >= 0) & (cols <= gw - 1)
            rvalid = (rows >= 0) & (rows <= gh - 1)
            if not (cvalid.any() and rvalid.any()):
                continue
            ci, ri = np.where(cvalid)[0], np.where(rvalid)[0]
            cc, rr = np.meshgrid(cols[ci], rows[ri])
            patch = map_coordinates(mmi, [rr, cc], order=1,
                                    mode="nearest").astype(np.float32)
            sub_i = out[ri[0]:ri[-1] + 1, ci[0]:ci[-1] + 1]
            sub_m = srcmag[ri[0]:ri[-1] + 1, ci[0]:ci[-1] + 1]
            better = patch > sub_i            # views write through to out/srcmag
            sub_i[better] = patch[better]
            sub_m[better] = emag              # attribute the cell to this event
            used += 1
            if used % 25 == 0:
                log(f"     merged {used} grids ({done}/{len(feats)} fetched)")
    log(f" [+] Composited {used} ShakeMap grids; MMI range "
        f"{out.min():.1f}-{out.max():.1f}; causative M "
        f"{srcmag[srcmag>0].min():.1f}-{srcmag.max():.1f}")
    np.savez_compressed(cache, mmi=out, mag=srcmag, used=used)
    return out, srcmag, used


# ---------------------------------------------------------------------------
# 5. Oblique 3D terrain rasteriser (painter's algorithm, far -> near)
# ---------------------------------------------------------------------------

def render_oblique(dem: np.ndarray, tex: np.ndarray, cell_m: float,
                   out_w: int, out_h: int) -> tuple[np.ndarray, np.ndarray]:
    """Perspective render with real height displacement and occlusion.

    Rows of the source grid run north (row 0, far) -> south (last row, near).
    Returns (rgb uint8, coverage mask uint8).
    """
    h, w = dem.shape
    z = np.maximum(dem, 0.0).astype(np.float32)               # sea rendered as a flat plane
    if GEOM_SMOOTH_M > 0:
        z = gaussian_filter(z, max(0.8, GEOM_SMOOTH_M / cell_m))
    # Taper the height field into the margins so the grid edge slopes away to sea
    # level instead of standing up as a cliff wall.
    z *= (1.0 - edge_falloff(h, w) ** 0.9)
    z *= VERT_EXAG

    # World space: x east, y up, z south(+) -- camera sits south of the map.
    half_w = (w - 1) * 0.5
    half_h = (h - 1) * 0.5
    extent_ns = (h - 1) * cell_m

    pitch = math.radians(CAM_PITCH_DEG)
    dist = extent_ns * CAM_DIST_FACTOR
    eye_y = dist * math.sin(pitch)
    eye_z = dist * math.cos(pitch)
    focal = (out_h * 0.5) / math.tan(math.radians(CAM_FOV_DEG) * 0.5)

    sp, cp = math.sin(pitch), math.cos(pitch)
    cx_scr, cy_scr = out_w * 0.5, out_h * 0.5

    wx = (np.arange(w, dtype=np.float32) - half_w) * cell_m       # constant per row
    # Pre-filled with the page colour: when the supersampled buffer is downsampled,
    # silhouette pixels then blend terrain against white rather than against black,
    # which would otherwise ring as a dark hairline around the whole map.
    rgb = np.full((out_h, out_w, 3), 255, np.uint8)
    cov = np.zeros((out_h, out_w), np.uint8)

    fz = focal * CAM_ZOOM
    pan = CAM_PAN_Y * out_h
    pan_x = CAM_PAN_X * out_w

    def project(row: int, elev_row: np.ndarray):
        wz = (row - half_h) * cell_m
        vx = wx
        vy = elev_row - eye_y
        vz = wz - eye_z
        cam_y = vy * cp - vz * sp
        cam_z = -(vy * sp + vz * cp)
        cam_z = np.maximum(cam_z, 1.0)
        sx = cx_scr + pan_x + fz * vx / cam_z
        sy = cy_scr + pan - fz * cam_y / cam_z
        return sx, sy

    log(f" [*] Rasterising {w}x{h} grid -> {out_w}x{out_h} ...")
    t_start = time.time()
    MAX_SKIRT = 420           # tall south-facing cliff faces under heavy exaggeration

    sx_cur, sy_cur = project(0, z[0])
    for r in range(h):
        if r + 1 < h:
            sx_nxt, sy_nxt = project(r + 1, z[r + 1])
        else:
            sx_nxt, sy_nxt = sx_cur, sy_cur + 2.0

        xi = np.rint(sx_cur).astype(np.int32)
        y_top = np.rint(sy_cur).astype(np.int32)
        y_bot = np.rint(np.maximum(sy_nxt, sy_cur + 1.0)).astype(np.int32)

        valid = (xi >= 0) & (xi < out_w) & (y_bot > 0) & (y_top < out_h)
        if np.any(valid):
            span = np.clip(y_bot - y_top, 1, MAX_SKIRT)
            kmax = int(span[valid].max())
            row_col = tex[r]
            deep = kmax > 3        # long skirts = south-facing cliff faces
            for k in range(kmax):
                yy = y_top + k
                m = valid & (k < span) & (yy >= 0) & (yy < out_h)
                if not np.any(m):
                    continue
                ym, xm = yy[m], xi[m]
                cm = row_col[m]
                if deep and k:
                    # shade the vertical face so cliffs do not smear as bright streaks
                    f = 1.0 - SKIRT_DARK * np.clip(k / np.maximum(span[m], 1), 0.0, 1.0)
                    cm = (cm.astype(np.float32) * f[:, None]).astype(np.uint8)
                rgb[ym, xm] = cm
                cov[ym, xm] = 255
                # 2px horizontal footprint closes magnification gaps on near rows
                xm2 = np.minimum(xm + 1, out_w - 1)
                rgb[ym, xm2] = cm
                cov[ym, xm2] = 255

        sx_cur, sy_cur = sx_nxt, sy_nxt
        if r % 800 == 0 and r:
            log(f"     row {r}/{h}  ({time.time() - t_start:.0f}s)")

    log(f" [+] Rasterised in {time.time() - t_start:.0f}s")
    return rgb, cov


# ---------------------------------------------------------------------------
# 6. Composition: cast shadow, background, typography
# ---------------------------------------------------------------------------

def _font(bold: bool, size: int) -> ImageFont.FreeTypeFont:
    names = ["georgiab.ttf", "cambriab.ttf"] if bold else ["georgia.ttf", "cambria.ttc"]
    for n in names:
        for d in (r"C:\Windows\Fonts", os.path.expanduser(r"~\AppData\Local\Microsoft\Windows\Fonts")):
            p = os.path.join(d, n)
            if os.path.exists(p):
                try:
                    return ImageFont.truetype(p, size)
                except Exception:
                    pass
    return ImageFont.load_default()


def draw_legend(d: ImageDraw.ImageDraw, x: int, y: int, scale: float,
                ramp, title: str, ticks, note: str) -> int:
    """Horizontal ramp legend. Returns the y of the bottom of the block."""
    f_lab = _font(True, int(25 * scale))
    f_tick = _font(False, int(23 * scale))
    f_note = _font(False, int(22 * scale))
    bar_w, bar_h = int(430 * scale), int(20 * scale)

    d.text((x, y), title, font=f_lab, fill=COL_TITLE)
    top = y + int(38 * scale)
    xs = [p for p, _ in ramp]
    for i in range(bar_w):
        t = i / float(bar_w - 1)
        col = tuple(int(np.interp(t, xs, [c[k] for _, c in ramp])) for k in range(3))
        d.line([(x + i, top), (x + i, top + bar_h)], fill=col)
    d.rectangle([x, top, x + bar_w, top + bar_h], outline=(120, 120, 132), width=1)

    for frac, label in ticks:
        tx = x + int(frac * bar_w)
        d.line([(tx, top + bar_h), (tx, top + bar_h + int(6 * scale))],
               fill=(120, 120, 132), width=1)
        tw = d.textlength(label, font=f_tick)
        d.text((tx - tw / 2, top + bar_h + int(10 * scale)), label,
               font=f_tick, fill=COL_CREDIT)

    ny = top + bar_h + int(42 * scale)
    for ln in note.split("\n"):
        d.text((x, ny), ln, font=f_note, fill=COL_CREDIT)
        ny += int(28 * scale)
    return ny


def compose(rgb: np.ndarray, cov: np.ndarray, n_quakes: int,
            out_w: int, out_h: int, mode: str = "epicentres") -> Image.Image:
    log(" [*] Compositing cast shadow and background ...")
    scale = out_w / 3840.0
    canvas = np.ones((out_h, out_w, 3), np.float32) * COL_BG[None, None, :]

    # Directional, streaky ground shadow (light from upper-left -> shadow lower-right).
    # Soft coverage (not a hard threshold) so the silhouette stays anti-aliased.
    m = cov.astype(np.float32) / 255.0
    shadow = np.zeros_like(m)
    steps = 26
    for i in range(1, steps + 1):
        dx = int(round(i * 3.4 * scale))
        dy = int(round(i * 2.0 * scale))
        sh = np.zeros_like(m)
        sh[dy:, dx:] = m[:m.shape[0] - dy if dy else None, :m.shape[1] - dx if dx else None]
        shadow += sh * (1.0 - i / (steps + 1.0))
    shadow = gaussian_filter(shadow / max(shadow.max(), 1e-6), 9.0 * scale)
    shadow = np.clip(shadow * 1.55, 0.0, 1.0) * 0.55
    shadow *= (1.0 - m)                       # never darken the terrain itself
    canvas += (COL_CAST_SHADOW[None, None, :] - canvas) * shadow[..., None]

    # Terrain over the top.
    mm = m[..., None]
    canvas = canvas * (1.0 - mm) + rgb.astype(np.float32) * mm

    img = Image.fromarray(np.clip(canvas, 0, 255).astype(np.uint8), "RGB")
    d = ImageDraw.Draw(img)

    f_head = _font(False, int(58 * scale))
    f_country = _font(True, int(156 * scale))
    f_credit = _font(False, int(25 * scale))

    x = int(118 * scale)
    y = int(190 * scale)
    if mode == "causative":
        head = [f"The earthquake behind your worst jolt ({SHAKE_YEAR_START}\u2013{YEAR_END - 1}):",
                "magnitude of the causative event"]
    elif mode == "shaking":
        head = [f"Ground shaking felt ({SHAKE_YEAR_START}\u2013{YEAR_END - 1}):",
                "strongest intensity on record"]
    else:
        head = [f"Earthquakes ({YEAR_START}\u2013{YEAR_END - 1}) with",
                f"magnitude {MAG_MIN} or higher"]
    d.text((x, y), head[0], font=f_head, fill=COL_TITLE)
    d.text((x, y + int(74 * scale)), head[1], font=f_head, fill=COL_TITLE)
    d.text((x - int(6 * scale), y + int(168 * scale)), "PAKISTAN",
           font=f_country, fill=COL_ACCENT)

    # ---- legend ----------------------------------------------------------
    ly = y + int(432 * scale)
    if mode == "causative":
        draw_legend(
            d, x, ly, scale, EQ_RAMP,
            "MAGNITUDE OF THE EARTHQUAKE THAT SHOOK THIS PLACE HARDEST",
            [(0.0, "M4.5"), (1 / 3, "M5.6"), (2 / 3, "M6.7"), (1.0, "M7.8")],
            "Magnitude belongs to the earthquake, not to a place, so intensity\n"
            "cannot be \"converted\" to magnitude pointwise. What is well defined\n"
            "is attribution: for each location, which event caused its strongest\n"
            "shaking, and how big that event was. Shown only where MMI IV+ was felt.")
    elif mode == "shaking":
        draw_legend(
            d, x, ly, scale, EQ_RAMP,
            "MODIFIED MERCALLI INTENSITY (MAXIMUM EXPERIENCED)",
            [(0.0, "IV"), (2 / 9, "V"), (4 / 9, "VI"), (6 / 9, "VII"),
             (8 / 9, "VIII+")],
            "Light \u00b7 Moderate \u00b7 Strong \u00b7 Very strong \u00b7 Severe\n"
            "Colour = how hard the ground shook at that place, not where\n"
            "the earthquake began. Shaking decays with distance from the\n"
            "source, so the plains are coloured even where no quake starts.")
    else:
        draw_legend(
            d, x, ly, scale, EQ_RAMP,
            "EPICENTRE DENSITY (WHERE RUPTURES BEGAN)",
            [(0.0, "isolated"), (0.5, "clustered"), (1.0, "dense")],
            "Colour = concentration of epicentres, i.e. the map point above\n"
            "where each rupture started. It is NOT a measure of shaking:\n"
            "a place can be violently shaken by a distant earthquake and\n"
            "still appear white here. See the companion intensity map.")

    credit = ([
        "Shaking: USGS ShakeMap / ShakeMap Atlas, cell-wise maximum MMI.",
        f"{n_quakes:,} events of M\u2265{SHAKE_MAG_MIN}, {SHAKE_YEAR_START}\u2013{YEAR_END - 1}.",
    ] if mode in ("shaking", "causative") else [
        "Seismicity: U.S. Geological Survey, Earthquake Hazards Program.",
        f"{n_quakes:,} events of M\u2265{MAG_MIN}, {YEAR_START}\u2013{YEAR_END - 1}.",
    ]) + [
        "Elevation & bathymetry: AWS Terrain Tiles (Mapzen/terrarium).",
        "Boundary: UN OCHA / HDX Pakistan ADM0.",
    ]
    cy = out_h - int(168 * scale)
    for lineno, ln in enumerate(credit):
        d.text((x, cy + lineno * int(34 * scale)), ln, font=f_credit, fill=COL_CREDIT)

    return img


# ---------------------------------------------------------------------------
# Optional: re-probe forge3d's 3D paths (kept for capable GPUs)
# ---------------------------------------------------------------------------

def probe_forge3d() -> None:
    try:
        import forge3d as f3d
    except ImportError:
        log(" [-] forge3d not installed; skipping probe.")
        return
    log(f" [*] forge3d {getattr(f3d, '__version__', '?')} | GPU: {f3d.has_gpu()}")
    for a in f3d.enumerate_adapters():
        log(f"     adapter: {a.get('name')} ({a.get('backend')})")
    try:
        hm = np.zeros((128, 128), np.float32)
        hm[32:96, 32:96] = 1.0
        cam = f3d.make_camera(origin=(64, 180, 260), look_at=(64, 0, 64), up=(0, 1, 0),
                              fov_y=40, aspect=1.5, exposure=1.0)
        f3d.hybrid_render_terrain_reference(hm, 384, 256, cam, spacing=(1, 1),
                                            exaggeration=40.0, sun_azimuth_deg=315,
                                            sun_elevation_deg=45, spp=32,
                                            max_frames=128, min_frames=16,
                                            variance_threshold=0.004)
        log(" [+] forge3d path tracer OK on this machine \u2014 it could drive the 3D pass.")
    except Exception as e:
        log(f" [-] forge3d path tracer unavailable here: {str(e)[:110]}")


# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", action="store_true", help="fast low-resolution pass")
    ap.add_argument("--mode", choices=["epicentres", "shaking", "causative"],
                    default="epicentres",
                    help="epicentres = where ruptures began; "
                         "shaking = maximum MMI actually experienced")
    args = ap.parse_args()

    t0 = time.time()
    log("=" * 78)
    log("PAKISTAN \u2014 OBLIQUE 3D SEISMICITY RELIEF MAP")
    log("=" * 78)

    if args.preview:
        src_w, out_w, out_h, ss = 1700, 1600, 900, 1
    else:
        src_w, out_w, out_h, ss = SRC_W, OUT_W, OUT_H, SUPERSAMPLE

    if USE_FORGE3D_PROBE:
        log("\n[0/6] Probing forge3d 3D capability ...")
        probe_forge3d()

    log("\n[1/6] Elevation + bathymetry ...")
    dem, cell_m = build_dem(src_w)

    log("\n[2/6] USGS earthquake catalogue ...")
    if args.mode in ("shaking", "causative"):
        mmi, srcmag, n_events = shakemap_composite(dem.shape[0], dem.shape[1])
        layer_fn = (shaking_layer(mmi) if args.mode == "shaking"
                    else causative_layer(srcmag, mmi))
    else:
        quakes = fetch_quakes()
        layer_fn = epicentre_layer(quakes)
        n_events = len(quakes)

    log("\n[3/6] Texture synthesis ...")
    tex = build_texture(dem, cell_m, layer_fn)
    gc.collect()

    log("\n[4/6] Oblique 3D render ...")
    rgb, cov = render_oblique(dem, tex, cell_m, out_w * ss, out_h * ss)
    del tex
    gc.collect()

    log("\n[5/6] Composition ...")
    if ss > 1:
        rgb = np.asarray(Image.fromarray(rgb).resize((out_w, out_h), Image.LANCZOS))
        cov = np.asarray(Image.fromarray(cov).resize((out_w, out_h), Image.LANCZOS))
    img = compose(rgb, cov, n_events, out_w, out_h, mode=args.mode)

    log("\n[6/6] Export ...")
    base = {"shaking": "pakistan_shaking_3d",
            "causative": "pakistan_causative_magnitude_3d"}.get(
                args.mode, "pakistan_earthquakes_3d")
    stem = base + ("_preview" if args.preview else "")
    png = os.path.join(OUT_DIR, stem + ".png")
    jpg = os.path.join(OUT_DIR, stem + ".jpg")
    img.save(png, "PNG")
    img.save(jpg, "JPEG", quality=95, subsampling=0)
    log(f" [+] {png}")
    log(f" [+] {jpg}")
    log(f"\n[DONE] {time.time() - t0:.0f}s total")


if __name__ == "__main__":
    main()
