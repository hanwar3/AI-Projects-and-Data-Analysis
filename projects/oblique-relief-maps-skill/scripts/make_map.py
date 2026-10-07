"""
make_map.py — end-to-end worked example. Copy this and edit; it is the fastest way
to a first frame.

    python make_map.py --bbox 59.6 21.2 78.4 37.4 --title "PAKISTAN" --quakes
    python make_map.py --bbox -10 35 5 45 --title "IBERIA" --preview

--preview renders small and fast (seconds) for look development. Drop it for the 4K
deliverable. Terrain tiles are cached, so only the first run pays the download.
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np
from PIL import ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from terrain import fetch_dem, grid_shape, lonlat_to_grid      # noqa: E402
from shading import TerrainCanvas, point_density, edge_falloff, RAMP_VIOLET  # noqa: E402
from oblique import (Camera, autoframe, render, compose, draw_title,  # noqa: E402
                     draw_legend, draw_credits)


def fetch_usgs(bbox, min_mag=4.0, start="1990-01-01", end="2026-01-01"):
    """[lon, lat, mag] for a bbox. Epicentres — where ruptures BEGAN, not shaking."""
    import csv
    import io as _io

    import requests
    lon0, lat0, lon1, lat1 = bbox
    url = ("https://earthquake.usgs.gov/fdsnws/event/1/query?format=csv"
           f"&starttime={start}&endtime={end}&minmagnitude={min_mag}"
           f"&minlatitude={lat0}&maxlatitude={lat1}"
           f"&minlongitude={lon0}&maxlongitude={lon1}")
    r = requests.get(url, timeout=180)
    r.raise_for_status()
    out = []
    for row in csv.DictReader(_io.StringIO(r.text)):
        try:
            out.append((float(row["longitude"]), float(row["latitude"]), float(row["mag"])))
        except (TypeError, ValueError, KeyError):
            continue
    print(f" [+] {len(out):,} events")
    return np.array(out, np.float32)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bbox", nargs=4, type=float, required=True,
                    metavar=("LON_MIN", "LAT_MIN", "LON_MAX", "LAT_MAX"))
    ap.add_argument("--title", default="REGION")
    ap.add_argument("--caption", nargs="*",
                    default=["Shaded relief and recorded", "earthquake epicentres"])
    ap.add_argument("--quakes", action="store_true", help="overlay USGS epicentres")
    ap.add_argument("--min-mag", type=float, default=4.0)
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--out", default="map.png")
    ap.add_argument("--cache", default="./_terrain_cache")
    args = ap.parse_args()

    bbox = tuple(args.bbox)
    if args.preview:
        src_w, out_w, out_h, ss, zoom = 1500, 1600, 900, 1, 8
    else:
        src_w, out_w, out_h, ss, zoom = 4200, 3840, 2160, 2, 9

    print("[1/4] terrain ...")
    dem, cell_m = fetch_dem(bbox, width=src_w, zoom=zoom, cache_dir=args.cache)

    print("[2/4] texture ...")
    canvas = TerrainCanvas(dem, cell_m)
    layers = []
    n_events = 0
    if args.quakes:
        pts = fetch_usgs(bbox, min_mag=args.min_mag)
        n_events = len(pts)
        if n_events:
            rows, cols = lonlat_to_grid(pts[:, 0], pts[:, 1], bbox, dem.shape)
            scale = dem.shape[1] / 5200.0
            rad = np.maximum(2.0, (np.maximum(pts[:, 2], 2.5) - 1.6) ** 1.42 * 3.1 * scale)
            amp = 0.58 + 0.24 * (np.maximum(pts[:, 2], 2.5) - 2.5) ** 1.28
            field = point_density(rows, cols, dem.shape, weights=amp,
                                  radius_px=rad, smooth=max(0.6, 0.8 * scale))
            alpha = np.clip(field * 3.1, 0.0, 1.0) ** 0.72 * 0.97
            layers.append((field, alpha, RAMP_VIOLET))
    tex = canvas.compose(layers=layers)

    print("[3/4] oblique render ...")
    taper = edge_falloff(*dem.shape)
    cam = autoframe(dem, cell_m, out_w * ss, out_h * ss, Camera())
    rgb, cov = render(dem, tex, cell_m, out_w * ss, out_h * ss, cam, edge_taper=taper)
    del tex

    print("[4/4] compose ...")
    img = compose(rgb, cov, out_w, out_h)
    scale = out_w / 3840.0
    d = draw_title(img, args.caption, args.title, scale=scale)
    if layers:
        draw_legend(d, int(118 * scale), int((190 + 432) * scale), scale, RAMP_VIOLET,
                    "EPICENTRE DENSITY (WHERE RUPTURES BEGAN)",
                    [(0.0, "isolated"), (0.5, "clustered"), (1.0, "dense")],
                    "Colour = concentration of epicentres. It is NOT a measure of\n"
                    "shaking: a place can be violently shaken by a distant\n"
                    "earthquake and still appear white here.")
        draw_credits(d, [f"Seismicity: USGS ComCat — {n_events:,} events, M≥{args.min_mag}.",
                         "Elevation & bathymetry: AWS Terrain Tiles (Mapzen/terrarium)."],
                     out_w, out_h, scale)
    img.save(args.out)
    print(f"[+] {args.out}")


if __name__ == "__main__":
    main()
