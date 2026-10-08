# Hazard maps: Pakistan and Texas

Oblique 3D shaded-relief maps of flood, earthquake and climate hazard, built in Python from open data and rendered at 4K. Each map answers one question, and the legend says what the colour is and what it is not.

| Map | Question it answers | Data | Script |
|---|---|---|---|
| [Pakistan floods, 2010 and 2022](maps/pakistan-floods.jpg) | Where did the two largest floods on record spread? | UN OCHA 2010 inundation, UNOSAT / VIIRS 2022 flood extent, Copernicus GLO-30 DEM, Natural Earth rivers | `code/Pak_Flood_2010.py` |
| [Pakistan earthquakes, 1900–2025](maps/pakistan-earthquakes.jpg) | Where do ruptures begin? | USGS ComCat, M ≥ 2.5 | `code/Pak_Earthquakes_3D.py` |
| [Pakistan ground shaking, 2000–2025](maps/pakistan-shaking.jpg) | Where has the ground actually shaken? | USGS ShakeMap, cell-wise maximum MMI across 309 events | `code/Pak_Earthquakes_3D.py` |
| [Magnitude of the causative event](maps/pakistan-causative-magnitude.jpg) | Which earthquake produced each place's worst shaking? | USGS ShakeMap + ComCat | `code/Pak_Earthquakes_3D.py` |
| [Seismicity statistics](maps/pakistan-seismicity-stats.jpg) | Is the catalogue complete, and what is the b-value? | USGS ComCat, queried with DuckDB | `code/make_analysis_figure.py` |
| [Pakistan temperature × rainfall](maps/pakistan-climate.jpg) | Where is it hot, wet, or both? | TerraClimate 2000–2021 normals (~4 km) | `code/Pak_Climate_Map.py`, `code/fetch_climate.py` |
| [Texas temperature × rainfall](maps/texas-climate.jpg) | Same question, Texas | TerraClimate 2000–2021 | `code/Texas_Hazard_Maps.py --map climate` |
| [Texas flood insurance claims](maps/texas-flood-nfip.jpg) | Where has flood damage actually been paid out? | FEMA OpenFEMA NFIP redacted claims, 2000–2025: 272,924 claims, $16.0B paid | `code/Texas_Hazard_Maps.py --map flood` |

## Report

[`reports/Pakistan_Seismicity_Report.pdf`](reports/Pakistan_Seismicity_Report.pdf) (11 pages) documents data sources, software, the processing pipeline and the analysis behind the earthquake maps. Main finding: the Punjab plain holds 61 catalogued epicentres against 840 in the Sulaiman–Kirthar fold belt at the same latitudes, yet 98.4% of Pakistan's land area has felt at least MMI IV since 2000 and 28.9% has felt MMI VI or more. An epicentre map understates exposure exactly where most people live.

## Method in brief

1. Elevation and bathymetry from AWS Terrain Tiles (terrarium encoding) or Copernicus GLO-30, resampled to an equal-ground-pixel grid.
2. Hazard layer fetched from its public API (USGS FDSN, ShakeMap grids, OpenFEMA, TerraClimate on Azure) and cached locally.
3. Multi-directional hillshade, relief-weighted tint and the data layer composited into one texture.
4. A purpose-built oblique renderer (perspective camera, real height displacement, occlusion) in NumPy. No GPU needed.
5. Typography, legend and credits composed with Pillow; 3840 × 2160 export.

The Pakistan climate and Texas maps use the renderer modules in `code/` (`terrain.py`, `shading.py`, `oblique.py`).

## Run

```bash
pip install numpy scipy pillow requests rasterio geopandas xarray adlfs duckdb matplotlib
python code/Texas_Hazard_Maps.py --map flood --preview   # fast low-res pass
python code/Texas_Hazard_Maps.py --map flood             # full 4K render
```

Data downloads are cached under `data/` on first run. Built with Claude Code as a pair programmer; data choices, map questions and checks are mine.
