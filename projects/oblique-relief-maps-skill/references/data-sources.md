# Data sources

Open, key-free sources that work from any environment, with the gotchas that cost time.

## Contents
- [Elevation and bathymetry](#elevation-and-bathymetry)
- [Earthquake catalogues (USGS ComCat)](#earthquake-catalogues-usgs-comcat)
- [Ground-shaking intensity (USGS ShakeMap)](#ground-shaking-intensity-usgs-shakemap)
- [Administrative boundaries](#administrative-boundaries)
- [Other thematic layers](#other-thematic-layers)
- [Verify provenance before trusting a local file](#verify-provenance-before-trusting-a-local-file)

---

## Elevation and bathymetry

**AWS Terrain Tiles (Mapzen "terrarium")** — `scripts/terrain.py` wraps this.

```
https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png
```

No API key, global coverage, and — the decisive advantage — **real ocean bathymetry**. For any coastal frame this is what lets the sea render as a physical surface instead of a masked hole. Decode with:

```
elevation_m = (R * 256 + G + B / 256) - 32768
```

Tiles are Web Mercator; resample to an equal-ground-pixel lon/lat grid (see `grid_shape` in `terrain.py`) or the map stretches vertically toward the poles.

Zoom choice — tile count scales 4× per level:

| Zoom | Ground res (mid-lat) | Tiles for ~19°×16° | Good for |
|---|---|---|---|
| 8 | ~530 m | ~200 | previews, continental frames |
| 9 | ~265 m | ~780 | country-scale 4K deliverables |
| 10 | ~130 m | ~3,100 | provincial / single-range detail |

**Alternatives, and why they usually lose here:** Copernicus GLO-30 and SRTM are finer but need mosaicking of many auth-gated tiles, and neither carries bathymetry. At country scale rendered to 4K, 30 m data is far past what the output can resolve — the whole country at 30 m is ~50,000 px across. Use them only for small, detailed extents.

## Earthquake catalogues (USGS ComCat)

```
https://earthquake.usgs.gov/fdsnws/event/1/query
    ?format=csv|geojson
    &starttime=1900-01-01&endtime=2026-01-01
    &minmagnitude=2.5
    &minlatitude=&maxlatitude=&minlongitude=&maxlongitude=
    &orderby=time
```

Swap `query` for `count` to size a request cheaply before pulling it. The service caps a single response at 20,000 events — page by time window if you exceed it.

Each record is an **origin**: longitude, latitude, depth, one magnitude, origin time. Nothing in it describes shaking anywhere else. Filter `type == 'earthquake'` to drop quarry blasts and explosions.

**Completeness is not uniform over time.** Global detection thresholds fell sharply when the digital network matured around 1973 — before that only roughly M≥5 was catalogued in most regions. A century-long epicentre map is therefore partly a map of instrument history. Check it directly:

```sql
SELECT EXTRACT(year FROM time) yr, COUNT(*), MIN(mag) FROM cat GROUP BY yr ORDER BY yr
```

If `MIN(mag)` steps down abruptly, say so on the map or restrict the window.

## Ground-shaking intensity (USGS ShakeMap)

This is what you need whenever the question is "who felt it", not "where did it start".

1. List events that have the product:
   `...fdsnws/event/1/query?format=geojson&producttype=shakemap&...`
2. For each, fetch the detail JSON and read
   `properties.products.shakemap[0].contents["download/grid.xml"].url`
3. Parse `grid.xml`: `<grid_specification>` gives `lon_min/lon_max/lat_min/lat_max/nlon/nlat`; `<grid_field>` names the columns; rows in `<grid_data>` run **latitude-descending, longitude-ascending**, so reshape to `(nlat, nlon)`.
4. Resample each event grid onto the working grid and take the **cell-wise maximum**.

Columns include `MMI`, `PGA`, `PGV`, `PSA03/10/30`. Native spacing is ~0.033° (~3.3 km) — coarser than most working grids, so a composite computed once can be resampled to other sizes rather than re-downloaded.

Grids run ~10 MB each. Stream them into the running maximum rather than caching raw XML; cache only the composite.

**Historic events** come from the *ShakeMap Atlas*, a reprocessed set for significant global earthquakes — the `source` field reads `atlas`.

**Track attribution while compositing.** Keep a parallel array recording which event set each cell's maximum, and its magnitude. That single extra array answers "what size earthquake caused the worst shaking here?", which is otherwise unanswerable (see `pitfalls.md`).

**Felt reports:** the same event JSON exposes DYFI (`felt`, `cdi`) — genuine public reports, expressed as **Community Decimal Intensity**, i.e. intensity, not magnitude.

## Administrative boundaries

- **UN OCHA / HDX** (`data.humdata.org`) — authoritative national and subnational sets, per-country, ADM0–ADM3. Best default for a national subject.
- **Natural Earth** (`naturalearthdata.com`) — 1:10m/50m/110m global, public domain, ideal when you need many countries at once.
- **OSM via geocoding** (`osmnx.geocode_to_gdf`) — convenient, but detail varies and it can be very high-vertex.

Rasterise to the working grid with `rasterio.features.rasterize` plus `rasterio.transform.from_bounds(lon_min, lat_min, lon_max, lat_max, w, h)`. Buffer the boundary before rasterising to get a line of controllable width.

Boundary depiction can be politically contested. Prefer the official national source when the map is about one country, and name the source in the credits.

## Other thematic layers

The pipeline is agnostic — any field on the working grid can be a layer:

- **Flood / inundation extents** — UNOSAT, Copernicus EMS, VIIRS/MODIS water products (vector → rasterise).
- **Population** — WorldPop, GHSL, Meta HRSL (already raster; resample).
- **Climate** — CHIRPS precipitation, ERA5 reanalysis, WorldClim.
- **Landcover / fire** — ESA WorldCover, MODIS/VIIRS burned area.
- **Any point set** — cities, incidents, observations → `point_density()`.

## Verify provenance before trusting a local file

A file named `*_dem_30m.tif` in a project directory is not evidence it is 30 m data. Always check:

```python
with rasterio.open(path) as s:
    print(s.res, s.bounds, s.crs, s.shape)
```

Compare the pixel size against the claim, and the bounds against the region you need. Truncated extents are common and quietly clip coastlines out of the frame.
