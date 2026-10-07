"""
Texas_Hazard_Maps.py
====================
Three oblique 3D relief maps of Texas, matching the Pakistan series:

  --map flood    NFIP paid flood-insurance claims, 2000-2025   (observed IMPACT)
  --map quake    Earthquake epicentres, M>=2.5, 1900-2025      (where ruptures BEGAN)
  --map climate  Mean annual temperature x rainfall, 2000-2021 (bivariate)

Timelines deliberately mirror the Pakistan maps: the seismic catalogue runs the full
instrumental record, the climate normals use TerraClimate's 2000-2021 coverage, and
the flood layer uses the modern 2000-2025 window.

Built on the `oblique-relief-maps` skill.

    py Texas_Hazard_Maps.py --map quake --preview
    py Texas_Hazard_Maps.py --map flood
"""

from __future__ import annotations

import argparse
import io
import os
import sys
import zipfile

import numpy as np
import requests
from PIL import ImageDraw
from scipy.ndimage import gaussian_filter, map_coordinates

# The renderer lives in the oblique-relief-maps skill. Use the copy in this repo if
# present, otherwise the installed Claude skill.
SKILL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..",
                     "oblique-relief-maps-skill", "scripts")
if not os.path.isdir(SKILL):
    SKILL = os.path.expanduser(r"~/.claude/skills/oblique-relief-maps/scripts")
sys.path.insert(0, SKILL)
from terrain import fetch_dem, lonlat_to_grid                          # noqa: E402
from shading import (TerrainCanvas, edge_falloff, point_density,       # noqa: E402
                     bivariate_rgb, RAMP_VIOLET)
from oblique import (Camera, autoframe, render, compose, draw_title,   # noqa: E402
                     draw_legend, draw_bivariate_legend, draw_credits)

BASE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE, "Texas Output Hazard Impacted Areas")
CACHE = os.path.join(BASE, "data", "texas_cache")
TILES = os.path.join(CACHE, "terrarium_z9")
os.makedirs(OUT_DIR, exist_ok=True)
os.makedirs(CACHE, exist_ok=True)

# Frame: Texas plus a band of the Gulf of Mexico so the coast reads as a real surface.
BBOX = (-107.0, 25.0, -93.0, 36.8)

RAMP_BLUE = [
    (0.00, np.array([205, 227, 245], np.float32)),
    (0.30, np.array([116, 178, 224], np.float32)),
    (0.60, np.array([38, 118, 189], np.float32)),
    (0.85, np.array([16, 71, 138], np.float32)),
    (1.00, np.array([9, 38, 84], np.float32)),
]
# climate bivariate corners: pale / yellow=heat / blue=rain / green=both
C_LO = (239, 237, 230)
C_WARM = (232, 194, 46)
C_WET = (30, 95, 168)
C_BOTH = (46, 125, 79)
CORNERS = (C_LO, C_WARM, C_WET, C_BOTH)

T_MIN, T_MAX = 10.0, 25.0        # degC — Texas range is far narrower than Pakistan's
P_MIN, P_MAX = 200.0, 1500.0     # mm/yr, log-scaled


# --------------------------------------------------------------------- data
def texas_masks(shape):
    """(inside 0/1, border line 0..1) from US Census TIGER state boundaries."""
    import geopandas as gpd
    from rasterio.features import rasterize
    from rasterio.transform import from_bounds
    shp_dir = os.path.join(CACHE, "tiger_states")
    shp = os.path.join(shp_dir, "tl_2023_us_state.shp")
    if not os.path.exists(shp):
        os.makedirs(shp_dir, exist_ok=True)
        print(" [*] downloading TIGER state boundaries ...")
        r = requests.get("https://www2.census.gov/geo/tiger/TIGER2023/STATE/tl_2023_us_state.zip",
                         timeout=300)
        r.raise_for_status()
        zipfile.ZipFile(io.BytesIO(r.content)).extractall(shp_dir)
    gdf = gpd.read_file(shp).to_crs("EPSG:4326")
    gdf = gdf[gdf["STUSPS"] == "TX"]
    h, w = shape
    tr = from_bounds(BBOX[0], BBOX[1], BBOX[2], BBOX[3], w, h)
    geoms = [g for g in gdf.geometry if g is not None and not g.is_empty]
    inside = rasterize([(g, 1) for g in geoms], out_shape=(h, w), transform=tr,
                       fill=0, dtype=np.uint8).astype(np.float32)
    lw = max(0.010, 0.010 * (5200.0 / w))
    line = rasterize([(g.boundary.buffer(lw), 1) for g in geoms], out_shape=(h, w),
                     transform=tr, fill=0, dtype=np.uint8).astype(np.float32)
    return inside, np.clip(gaussian_filter(line, 0.7), 0.0, 1.0)


def fetch_quakes():
    """[lon, lat, mag] epicentres, M>=2.5, 1900-2025."""
    p = os.path.join(CACHE, "tx_quakes.npy")
    if os.path.exists(p):
        return np.load(p)
    import csv
    lon0, lat0, lon1, lat1 = BBOX
    url = ("https://earthquake.usgs.gov/fdsnws/event/1/query?format=csv"
           "&starttime=1900-01-01&endtime=2026-01-01&minmagnitude=2.5"
           f"&minlatitude={lat0}&maxlatitude={lat1}"
           f"&minlongitude={lon0}&maxlongitude={lon1}&orderby=time")
    print(" [*] querying USGS ComCat ...")
    r = requests.get(url, timeout=300)
    r.raise_for_status()
    out = []
    for row in csv.DictReader(io.StringIO(r.text)):
        if row.get("type") != "earthquake":
            continue
        try:
            out.append((float(row["longitude"]), float(row["latitude"]),
                        float(row["mag"]), int(row["time"][:4])))
        except (TypeError, ValueError, KeyError):
            continue
    a = np.array(out, np.float32)
    np.save(p, a)
    print(f" [+] {len(a):,} events")
    return a


def fetch_claims():
    """[lon, lat, total_paid] NFIP paid claims, 2000-2025 — observed flood impact."""
    p = os.path.join(CACHE, "tx_nfip_claims.npy")
    if os.path.exists(p):
        return np.load(p)
    base = ("https://www.fema.gov/api/open/v2/FimaNfipClaims"
            "?$filter=state%20eq%20%27TX%27%20and%20yearOfLoss%20ge%202000"
            "&$select=latitude,longitude,amountPaidOnBuildingClaim,"
            "amountPaidOnContentsClaim&$top=10000&$skip=")
    rows, skip = [], 0
    print(" [*] paging OpenFEMA NFIP claims ...")
    while True:
        r = requests.get(base + str(skip), timeout=300)
        r.raise_for_status()
        chunk = r.json().get("FimaNfipClaims", [])
        if not chunk:
            break
        for c in chunk:
            la, lo = c.get("latitude"), c.get("longitude")
            if la is None or lo is None:
                continue
            paid = (c.get("amountPaidOnBuildingClaim") or 0.0) + \
                   (c.get("amountPaidOnContentsClaim") or 0.0)
            rows.append((lo, la, paid))
        skip += len(chunk)          # advance by what the API actually returned
        if skip % 50000 < len(chunk):
            print(f"     {skip:,} ...")
    a = np.array(rows, np.float32)
    np.save(p, a)
    print(f" [+] {len(a):,} claims, ${a[:, 2].sum()/1e9:.1f}B paid")
    return a


def fetch_climate():
    """(tavg degC, ppt mm/yr, lat, lon) TerraClimate 2000-2021 normals."""
    p = os.path.join(CACHE, "tx_climate_2000_2021.npz")
    if os.path.exists(p):
        z = np.load(p)
        return z["tavg"], z["ppt"], z["lat"], z["lon"]
    import xarray as xr
    import adlfs
    tok = requests.get(
        "https://planetarycomputer.microsoft.com/api/sas/v1/token/cpdataeuwest/cpdata",
        timeout=60).json()["token"]
    fs = adlfs.AzureBlobFileSystem(account_name="cpdataeuwest", credential=tok)
    ds = xr.open_zarr(fs.get_mapper("cpdata/terraclimate.zarr"), consolidated=True)
    sub = ds.sel(lat=slice(BBOX[3], BBOX[1]), lon=slice(BBOX[0], BBOX[2]),
                 time=slice("2000-01-01", "2021-12-31"))
    print(" [*] TerraClimate temperature ...")
    tavg = ((sub["tmax"] + sub["tmin"]) / 2.0).mean("time").compute()
    print(" [*] TerraClimate precipitation ...")
    ppt = sub["ppt"].groupby("time.year").sum("time").mean("year").compute()
    np.savez_compressed(p, tavg=tavg.values.astype(np.float32),
                        ppt=ppt.values.astype(np.float32),
                        lat=tavg.lat.values, lon=tavg.lon.values)
    return tavg.values, ppt.values, tavg.lat.values, ppt.lon.values


def regrid(field, src_lat, src_lon, shape):
    h, w = shape
    r = np.interp(np.linspace(BBOX[3], BBOX[1], h), src_lat[::-1],
                  np.arange(len(src_lat))[::-1])
    c = np.interp(np.linspace(BBOX[0], BBOX[2], w), src_lon, np.arange(len(src_lon)))
    cc, rr = np.meshgrid(c, r)
    return map_coordinates(np.nan_to_num(field, nan=0.0), [rr, cc],
                           order=1, mode="nearest").astype(np.float32)


# --------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--map", choices=["flood", "quake", "climate"], required=True)
    ap.add_argument("--preview", action="store_true")
    args = ap.parse_args()
    if args.preview:
        src_w, out_w, out_h, ss, zoom = 1600, 1600, 900, 1, 8
    else:
        src_w, out_w, out_h, ss, zoom = 4200, 3840, 2160, 2, 9

    print("[1/4] terrain ...")
    dem, cell_m = fetch_dem(BBOX, width=src_w, zoom=zoom, cache_dir=CACHE)
    inside, line = texas_masks(dem.shape)
    scale_px = dem.shape[1] / 5200.0

    print("[2/4] layer ...")
    canvas = TerrainCanvas(dem, cell_m)
    legend = None

    if args.map == "quake":
        q = fetch_quakes()
        modern = int((q[:, 3] >= 2008).sum())
        rows, cols = lonlat_to_grid(q[:, 0], q[:, 1], BBOX, dem.shape)
        rad = np.maximum(3.0, (np.maximum(q[:, 2], 2.5) - 1.6) ** 1.42 * 3.4 * scale_px)
        amp = 0.58 + 0.24 * (np.maximum(q[:, 2], 2.5) - 2.5) ** 1.28
        field = point_density(rows, cols, dem.shape, weights=amp, radius_px=rad,
                              smooth=max(0.8, 1.0 * scale_px))
        alpha = np.clip(field * 3.1, 0.0, 1.0) ** 0.72 * (0.62 + 0.38 * inside) * 0.97
        layers = [(field, alpha, RAMP_VIOLET)]
        caption = ["Earthquake epicentres (1900–2025),", "magnitude 2.5 or higher"]
        legend = ("EPICENTRE DENSITY (WHERE RUPTURES BEGAN)", RAMP_VIOLET,
                  [(0.0, "isolated"), (0.5, "clustered"), (1.0, "dense")],
                  "Colour = concentration of epicentres, not shaking. "
                  f"{modern:,} of\n{len(q):,} events ({100*modern/len(q):.0f}%) "
                  "occurred after 2008 — Texas\nseismicity is dominated by "
                  "injection-induced earthquakes in\nthe Permian and Fort Worth "
                  "basins, and by the TexNet network\nthat began recording them in 2017.")
        credits = [f"Seismicity: USGS ComCat — {len(q):,} events, M≥2.5, 1900–2025.",
                   "Elevation & bathymetry: AWS Terrain Tiles (Mapzen/terrarium).",
                   "Boundary: US Census TIGER/Line 2023."]

    elif args.map == "flood":
        cl = fetch_claims()
        rows, cols = lonlat_to_grid(cl[:, 0], cl[:, 1], BBOX, dem.shape)
        # Claim coordinates are published rounded to 0.1 deg (~11 km) for privacy, so
        # the kernel must be wider than that lattice or it renders as a dot grid.
        rad = max(12.0, 0.11 / (BBOX[2] - BBOX[0]) * dem.shape[1])
        w = np.clip(cl[:, 2], 0, None) ** 0.4          # damp a very long-tailed payout
        field = point_density(rows, cols, dem.shape, weights=w, radius_px=rad,
                              smooth=max(2.0, 3.0 * scale_px), percentile=99.0)
        # Harris County alone dwarfs every other cluster, so a linear ramp renders the
        # rest of the state blank. Log-stretch to bring out the secondary centres.
        K = 40.0
        field = np.log1p(field * K) / np.log1p(K)
        alpha = np.clip(field * 2.6, 0.0, 1.0) ** 0.70 * (0.55 + 0.45 * inside) * 0.95
        layers = [(field, alpha, RAMP_BLUE)]
        caption = ["Flood damage actually paid out,", "2000–2025"]
        legend = ("NFIP PAID FLOOD CLAIMS (OBSERVED IMPACT)", RAMP_BLUE,
                  [(0.0, "few"), (0.5, "many"), (1.0, "Harris Co.")],
                  "Colour = density of paid flood-insurance claims, weighted\n"
                  "by payout. This is realised damage, not modelled hazard:\n"
                  "it reflects where people, property and insurance are, so\n"
                  "unmapped rural floodplains can be blank yet still flood.\n"
                  f"{len(cl):,} claims, ${cl[:, 2].sum()/1e9:.1f}B paid.")
        credits = [f"Flood impact: FEMA OpenFEMA NFIP redacted claims — {len(cl):,} "
                   f"claims, 2000–2025 (coords rounded to 0.1°).",
                   "Elevation & bathymetry: AWS Terrain Tiles (Mapzen/terrarium).",
                   "Boundary: US Census TIGER/Line 2023."]

    else:
        tavg_s, ppt_s, la, lo = fetch_climate()
        tavg = regrid(tavg_s, la, lo, dem.shape)
        ppt = regrid(ppt_s, la, lo, dem.shape)
        print(f"    T {tavg.min():.1f}..{tavg.max():.1f} degC | "
              f"P {ppt.min():.0f}..{ppt.max():.0f} mm/yr")
        fx = np.clip((tavg - T_MIN) / (T_MAX - T_MIN), 0.0, 1.0)
        lp = np.log10(np.clip(ppt, P_MIN, P_MAX))
        fy = (lp - np.log10(P_MIN)) / (np.log10(P_MAX) - np.log10(P_MIN))
        rgbl = bivariate_rgb(fx, fy, CORNERS)
        alpha = np.clip(gaussian_filter(inside, max(2.0, dem.shape[1] / 320.0)) * 1.15,
                        0.0, 1.0) * 0.80
        layers = [(rgbl, alpha, None)]
        caption = ["Mean annual temperature and rainfall,", "2000–2021"]
        credits = ["Climate: TerraClimate (Abatzoglou et al. 2018), 1/24° (~4 km), "
                   "2000–2021 normals.",
                   "Elevation & bathymetry: AWS Terrain Tiles (Mapzen/terrarium).",
                   "Boundary: US Census TIGER/Line 2023.  Layer clipped to state."]

    tex = canvas.compose(layers=layers, focus=inside, focus_fade=0.50, border=line)
    del layers

    print("[3/4] oblique render ...")
    taper = edge_falloff(*dem.shape)
    cam = autoframe(dem, cell_m, out_w * ss, out_h * ss, Camera())
    rgbi, cov = render(dem, tex, cell_m, out_w * ss, out_h * ss, cam, edge_taper=taper)
    del tex

    print("[4/4] compose ...")
    img = compose(rgbi, cov, out_w, out_h)
    sc = out_w / 3840.0
    d = draw_title(img, caption, "TEXAS", scale=sc)
    if legend:
        title, ramp, ticks, note = legend
        draw_legend(d, int(126 * sc), int(640 * sc), sc, ramp, title, ticks, note)
    else:
        draw_bivariate_legend(
            img, int(250 * sc), int(660 * sc), sc, CORNERS,
            "MEAN ANNUAL TEMPERATURE  (warmer to the right)", "MEAN ANNUAL RAINFALL",
            [(0.0, "10°C"), (0.5, "17°C"), (1.0, "25°C")],
            [(0.0, "200 mm"), (0.45, "500"), (0.75, "1000"), (1.0, "1500+")],
            note="Colour shows both variables at once: warmer to the right,\n"
                 "wetter upward. Rainfall is log-scaled.",
            size=340,
            corner_notes=[("Neither — cool & dry: western High Plains", C_LO),
                          ("Yellow = heat only: Trans-Pecos, lower Rio Grande", C_WARM),
                          ("Blue = rain only: none at this scale", C_WET),
                          ("Green = both: East Texas, Gulf coast", C_BOTH)])
    draw_credits(ImageDraw.Draw(img), credits, out_w, out_h, sc)

    stem = f"texas_{args.map}" + ("_preview" if args.preview else "")
    img.save(os.path.join(OUT_DIR, stem + ".png"))
    img.save(os.path.join(OUT_DIR, stem + ".jpg"), quality=95, subsampling=0)
    print(f"[+] {os.path.join(OUT_DIR, stem + '.png')}")


if __name__ == "__main__":
    main()
