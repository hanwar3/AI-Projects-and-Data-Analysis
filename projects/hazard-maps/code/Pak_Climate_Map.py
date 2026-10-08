"""
Pak_Climate_Map.py
==================
Bivariate climate map of Pakistan: mean annual temperature x mean annual precipitation,
TerraClimate 2000-2021, draped on oblique 3D shaded relief.

Uses the oblique renderer in terrain.py, shading.py and oblique.py.

Colour scheme (four named corners, per request):
    cool + dry  -> pale neutral  (high cold deserts: Karakoram, Chagai)
    warm + dry  -> yellow        (heat only: Thar, lower Indus, Balochistan)
    cool + wet  -> blue          (rain only: northern mountain fringe)
    warm + wet  -> green         (both: Punjab/KP monsoon belt)

Run:
    py Pak_Climate_Map.py --preview
    py Pak_Climate_Map.py
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np
from PIL import ImageDraw
from scipy.ndimage import gaussian_filter, map_coordinates

# Renderer modules (terrain.py, shading.py, oblique.py) sit next to this script.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from terrain import fetch_dem                                        # noqa: E402
from shading import TerrainCanvas, edge_falloff, bivariate_rgb       # noqa: E402
from oblique import (Camera, autoframe, render, compose, draw_title,  # noqa: E402
                     draw_bivariate_legend, draw_credits)

BASE = os.path.dirname(os.path.abspath(__file__))
BBOX = (59.6, 21.2, 78.4, 37.4)
CLIM = os.path.join(BASE, "data", "analysis", "pak_climate_2000_2021.npz")
CACHE = os.path.join(BASE, "data", "eq3d_cache", "terrarium_z9")
OUT_DIR = os.path.join(BASE, "outputs")

# Scale limits. Precipitation is log-scaled: Pakistan spans roughly 50 mm in the Thar
# to >1500 mm on the Himalayan front, and a linear ramp would collapse the entire arid
# three-quarters of the country into one flat colour.
T_MIN, T_MAX = -8.0, 28.0            # degC
P_MIN, P_MAX = 60.0, 1200.0          # mm/yr, log-scaled

C_COOL_DRY = (239, 237, 230)         # pale neutral (neither hot nor wet)
C_WARM_DRY = (232, 194, 46)          # yellow  = warm only
C_COOL_WET = (30, 95, 168)           # blue    = rain only
C_WARM_WET = (46, 125, 79)           # green   = warm AND rainy
CORNERS = (C_COOL_DRY, C_WARM_DRY, C_COOL_WET, C_WARM_WET)


def pakistan_masks(shape):
    """(inside 0/1, border line 0..1) on the working grid."""
    import geopandas as gpd
    from rasterio.features import rasterize
    from rasterio.transform import from_bounds
    h, w = shape
    src = os.path.join(BASE, "data", "admin_boundaries", "pak_admin0.shp")
    if not os.path.exists(src):
        src = os.path.join(BASE, "data", "pakistan_boundary.geojson")
    gdf = gpd.read_file(src).to_crs("EPSG:4326")
    tr = from_bounds(BBOX[0], BBOX[1], BBOX[2], BBOX[3], w, h)
    geoms = [g for g in gdf.geometry if g is not None and not g.is_empty]
    inside = rasterize([(g, 1) for g in geoms], out_shape=(h, w), transform=tr,
                       fill=0, dtype=np.uint8).astype(np.float32)
    lw = max(0.010, 0.010 * (5200.0 / w))
    line = rasterize([(g.boundary.buffer(lw), 1) for g in geoms], out_shape=(h, w),
                     transform=tr, fill=0, dtype=np.uint8).astype(np.float32)
    return inside, np.clip(gaussian_filter(line, 0.7), 0.0, 1.0)


def regrid(field, src_lat, src_lon, shape):
    """Bilinear resample a lat/lon field onto the working grid (rows north->south)."""
    h, w = shape
    tgt_lat = np.linspace(BBOX[3], BBOX[1], h)
    tgt_lon = np.linspace(BBOX[0], BBOX[2], w)
    # source lat is descending, lon ascending
    r = np.interp(tgt_lat, src_lat[::-1], np.arange(len(src_lat))[::-1])
    c = np.interp(tgt_lon, src_lon, np.arange(len(src_lon)))
    cc, rr = np.meshgrid(c, r)
    return map_coordinates(np.nan_to_num(field, nan=0.0), [rr, cc],
                           order=1, mode="nearest").astype(np.float32)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", action="store_true")
    args = ap.parse_args()
    if args.preview:
        src_w, out_w, out_h, ss, zoom = 1700, 1600, 900, 1, 9
    else:
        src_w, out_w, out_h, ss, zoom = 4200, 3840, 2160, 2, 9

    print("[1/5] terrain ...")
    dem, cell_m = fetch_dem(BBOX, width=src_w, zoom=zoom,
                            cache_dir=os.path.dirname(CACHE))

    print("[2/5] climate ...")
    z = np.load(CLIM)
    tavg = regrid(z["tavg"], z["lat"], z["lon"], dem.shape)
    ppt = regrid(z["ppt"], z["lat"], z["lon"], dem.shape)
    yrs = z["years"]
    print(f"    T {tavg.min():.1f}..{tavg.max():.1f} degC | "
          f"P {ppt.min():.0f}..{ppt.max():.0f} mm/yr")

    fx = np.clip((tavg - T_MIN) / (T_MAX - T_MIN), 0.0, 1.0)
    lp = np.log10(np.clip(ppt, P_MIN, P_MAX))
    fy = (lp - np.log10(P_MIN)) / (np.log10(P_MAX) - np.log10(P_MIN))
    rgb_layer = bivariate_rgb(fx, fy, CORNERS)

    print("[3/5] texture ...")
    inside, line = pakistan_masks(dem.shape)
    alpha = gaussian_filter(inside, max(2.0, dem.shape[1] / 320.0))
    alpha = np.clip(alpha * 1.15, 0.0, 1.0) * 0.80
    canvas = TerrainCanvas(dem, cell_m)
    tex = canvas.compose(layers=[(rgb_layer, alpha, None)],
                         focus=inside, focus_fade=0.26, border=line)
    del rgb_layer

    print("[4/5] oblique render ...")
    taper = edge_falloff(*dem.shape)
    cam = autoframe(dem, cell_m, out_w * ss, out_h * ss, Camera())
    rgbi, cov = render(dem, tex, cell_m, out_w * ss, out_h * ss, cam, edge_taper=taper)
    del tex

    print("[5/5] compose ...")
    img = compose(rgbi, cov, out_w, out_h)
    scale = out_w / 3840.0
    d = draw_title(img, [f"Mean annual temperature and rainfall,",
                         f"{yrs[0]}–{yrs[1]}"], "PAKISTAN", scale=scale)
    draw_bivariate_legend(
        img, int(250 * scale), int(700 * scale), scale, CORNERS,
        "MEAN ANNUAL TEMPERATURE  (warmer to the right)", "MEAN ANNUAL RAINFALL",
        [(0.0, "−8°C"), (0.32, "5°C"), (0.62, "15°C"), (1.0, "28°C")],
        [(0.0, "60 mm"), (0.36, "150"), (0.69, "500"), (1.0, "1200+")],
        note="Colour shows both variables at once: read across for heat,\n"
             "up for rain. Rainfall is log-scaled. Relief is shaded terrain.",
        size=350,
        corner_notes=[("Neither — cool & dry: Karakoram, Chagai", C_COOL_DRY),
                      ("Yellow = heat only: Thar, lower Indus, Makran", C_WARM_DRY),
                      ("Blue = rain only: northern mountain fringe", C_COOL_WET),
                      ("Green = both: Punjab/KP monsoon belt", C_WARM_WET)])
    draw_credits(ImageDraw.Draw(img), [
        f"Climate: TerraClimate (Abatzoglou et al. 2018), 1/24° (~4 km), {yrs[0]}–{yrs[1]} normals.",
        "Elevation & bathymetry: AWS Terrain Tiles (Mapzen/terrarium).",
        "Boundary: UN OCHA / HDX Pakistan ADM0.  Climate layer clipped to national territory.",
    ], out_w, out_h, scale)

    os.makedirs(OUT_DIR, exist_ok=True)
    stem = "pakistan_climate_bivariate" + ("_preview" if args.preview else "")
    img.save(os.path.join(OUT_DIR, stem + ".png"))
    img.save(os.path.join(OUT_DIR, stem + ".jpg"), quality=95, subsampling=0)
    print(f"[+] {os.path.join(OUT_DIR, stem + '.png')}")


if __name__ == "__main__":
    main()
