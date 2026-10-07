"""
fetch_climate.py — TerraClimate 2000-2021 climate normals for Pakistan.

Mean annual temperature  = mean over all months of (tmax + tmin) / 2   [degC]
Mean annual precipitation = mean over years of the 12-month total       [mm/yr]

Source: TerraClimate (Abatzoglou et al. 2018), 1/24 deg (~4 km), read from the
Microsoft Planetary Computer Zarr store with a free anonymous SAS token. Only the
chunks covering the bbox are transferred.
"""
import os
import time

import numpy as np
import requests
import xarray as xr
import adlfs

BBOX = (59.6, 21.2, 78.4, 37.4)     # lon0, lat0, lon1, lat1
Y0, Y1 = 2000, 2021                 # Zarr coverage ends 2021-12
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "analysis")
os.makedirs(OUT, exist_ok=True)

tok = requests.get(
    "https://planetarycomputer.microsoft.com/api/sas/v1/token/cpdataeuwest/cpdata",
    timeout=60).json()["token"]
fs = adlfs.AzureBlobFileSystem(account_name="cpdataeuwest", credential=tok)
ds = xr.open_zarr(fs.get_mapper("cpdata/terraclimate.zarr"), consolidated=True)

lon0, lat0, lon1, lat1 = BBOX
sub = ds.sel(lat=slice(lat1, lat0), lon=slice(lon0, lon1),
             time=slice(f"{Y0}-01-01", f"{Y1}-12-31"))
print("subset", dict(sub.sizes), flush=True)

t = time.time()
print("[1/2] temperature ...", flush=True)
tavg = ((sub["tmax"] + sub["tmin"]) / 2.0).mean("time").compute()
print(f"    {time.time() - t:.0f}s  range {float(tavg.min()):.1f}..{float(tavg.max()):.1f} degC",
      flush=True)

t = time.time()
print("[2/2] precipitation ...", flush=True)
ppt = sub["ppt"].groupby("time.year").sum("time").mean("year").compute()
print(f"    {time.time() - t:.0f}s  range {float(ppt.min()):.0f}..{float(ppt.max()):.0f} mm/yr",
      flush=True)

np.savez_compressed(
    os.path.join(OUT, "pak_climate_2000_2021.npz"),
    tavg=tavg.values.astype(np.float32),
    ppt=ppt.values.astype(np.float32),
    lat=tavg.lat.values.astype(np.float64),
    lon=tavg.lon.values.astype(np.float64),
    years=np.array([Y0, Y1]),
)
print("[+] saved", os.path.join(OUT, "pak_climate_2000_2021.npz"), flush=True)
