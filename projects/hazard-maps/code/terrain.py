"""
terrain.py — elevation + bathymetry for any bounding box, as an equal-ground-pixel grid.

Uses AWS Terrain Tiles (Mapzen "terrarium" encoding): global, no API key, and — the
reason to prefer it over SRTM/Copernicus for this kind of map — it carries real ocean
bathymetry, so coastal frames render the sea as a surface instead of a hole.

    from terrain import fetch_dem
    dem, cell_m = fetch_dem((59.6, 21.2, 78.4, 37.4), width=4200)

Requires: numpy, requests, Pillow, scipy.
"""

from __future__ import annotations

import io
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import requests
from PIL import Image
from scipy.ndimage import map_coordinates

TILE_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"


def _deg2tile(lon: float, lat: float, z: int) -> tuple[float, float]:
    n = 2.0 ** z
    x = (lon + 180.0) / 360.0 * n
    lat_r = math.radians(lat)
    y = (1.0 - math.log(math.tan(lat_r) + 1.0 / math.cos(lat_r)) / math.pi) / 2.0 * n
    return x, y


def _fetch_tile(job):
    z, x, y, cache_dir = job
    path = os.path.join(cache_dir, f"{z}_{x}_{y}.png") if cache_dir else None
    if path and os.path.exists(path):
        try:
            return x, y, np.asarray(Image.open(path).convert("RGB"))
        except Exception:
            os.remove(path)
    url = TILE_URL.format(z=z, x=x, y=y)
    for attempt in range(4):
        try:
            r = requests.get(url, timeout=30)
            if r.status_code == 404:
                return x, y, None
            r.raise_for_status()
            if path:
                with open(path, "wb") as fh:
                    fh.write(r.content)
            return x, y, np.asarray(Image.open(io.BytesIO(r.content)).convert("RGB"))
        except Exception:
            time.sleep(0.6 * (attempt + 1))
    return x, y, None


def grid_shape(bbox, width: int) -> tuple[int, int, float]:
    """Rows, cols and ground cell size (m) for an equal-ground-pixel grid.

    Longitude degrees shrink with latitude, so a naive lon/lat grid stretches the map
    vertically. Sizing the grid by ground distance keeps the render undistorted without
    needing a projected CRS.
    """
    lon0, lat0, lon1, lat1 = bbox
    lat_mid = 0.5 * (lat0 + lat1)
    ground_w = (lon1 - lon0) * 111320.0 * math.cos(math.radians(lat_mid))
    ground_h = (lat1 - lat0) * 110570.0
    height = int(round(width * ground_h / ground_w))
    return height, width, ground_w / width


def fetch_dem(bbox, width: int = 4200, zoom: int = 9, cache_dir: str | None = None,
              workers: int = 16, verbose: bool = True):
    """Return (dem float32 [north->south rows], ground cell size in metres).

    bbox is (lon_min, lat_min, lon_max, lat_max). Pick `zoom` so tile resolution is a
    little finer than the working grid: z=9 is ~265 m at mid-latitudes and suits a
    country-scale 4K render. Higher zooms multiply tile count by 4 each step.
    """
    lon0, lat0, lon1, lat1 = bbox
    height, width, cell_m = grid_shape(bbox, width)

    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
        cache = os.path.join(cache_dir, f"dem_{width}x{height}_z{zoom}.npy")
        if os.path.exists(cache):
            if verbose:
                print(f" [+] DEM cache hit {width}x{height} @ {cell_m:,.0f} m/px")
            return np.load(cache), cell_m

    x0f, y1f = _deg2tile(lon0, lat0, zoom)
    x1f, y0f = _deg2tile(lon1, lat1, zoom)
    x0, x1 = int(math.floor(x0f)), int(math.floor(x1f))
    y0, y1 = int(math.floor(y0f)), int(math.floor(y1f))
    jobs = [(zoom, x, y, cache_dir) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)]
    if verbose:
        print(f" [*] fetching {len(jobs)} terrain tiles (z={zoom}) ...")

    nx, ny = (x1 - x0 + 1), (y1 - y0 + 1)
    mosaic = np.zeros((ny * 256, nx * 256), np.float32)
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for x, y, rgb in pool.map(_fetch_tile, jobs):
            done += 1
            if verbose and done % 100 == 0:
                print(f"     {done}/{len(jobs)}")
            if rgb is None:
                continue
            a = rgb.astype(np.float32)
            # terrarium encoding
            elev = (a[:, :, 0] * 256.0 + a[:, :, 1] + a[:, :, 2] / 256.0) - 32768.0
            mosaic[(y - y0) * 256:(y - y0) * 256 + 256,
                   (x - x0) * 256:(x - x0) * 256 + 256] = elev

    # Resample the Web-Mercator mosaic onto the regular lon/lat working grid.
    lons = np.linspace(lon0, lon1, width, dtype=np.float64)
    lats = np.linspace(lat1, lat0, height, dtype=np.float64)      # row 0 = north
    n = 2.0 ** zoom
    tx = (lons + 180.0) / 360.0 * n
    lat_r = np.radians(lats)
    ty = (1.0 - np.log(np.tan(lat_r) + 1.0 / np.cos(lat_r)) / np.pi) / 2.0 * n
    cc, rr = np.meshgrid((tx - x0) * 256.0, (ty - y0) * 256.0)
    dem = map_coordinates(mosaic, [rr, cc], order=1, mode="nearest").astype(np.float32)
    del mosaic, cc, rr

    if cache_dir:
        np.save(cache, dem)
    if verbose:
        print(f" [+] DEM {width}x{height} @ {cell_m:,.0f} m/px "
              f"(min {dem.min():,.0f} m, max {dem.max():,.0f} m)")
    return dem, cell_m


def lonlat_to_grid(lon, lat, bbox, shape):
    """Vectorised lon/lat -> (row, col) on the working grid."""
    lon0, lat0, lon1, lat1 = bbox
    h, w = shape
    col = (np.asarray(lon) - lon0) / (lon1 - lon0) * (w - 1)
    row = (lat1 - np.asarray(lat)) / (lat1 - lat0) * (h - 1)
    return row, col
