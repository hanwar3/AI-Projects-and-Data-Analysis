---
name: oblique-relief-maps
description: >
  Build publication-quality oblique 3D shaded-relief maps — a tilted terrain slab on a
  white page with data draped over it, in the style of Milos Popovic / rayshader relief
  plates — plus the technical report that documents them. Covers sourcing elevation and
  bathymetry, overlaying point or raster data (earthquakes, floods, population, climate,
  hazard intensity), the cartographic recipe, and a fast CPU renderer that needs no GPU.
  Use this skill whenever the user wants a terrain, relief, topographic, hillshade, 3D,
  or "Milos-style" map; whenever they want to map earthquakes, seismicity, shaking,
  flooding, hazard, elevation, or any geographic data over terrain; whenever they mention
  forge3d, rayshader, DEMs, ShakeMap, USGS catalogues, or hypsometric tints; and whenever
  they ask for a map that "looks like" a reference image of 3D terrain. Also use it when
  someone asks whether a map is showing the right variable — it carries the
  origin-versus-impact reasoning that keeps these maps honest.
---

# Oblique relief maps

Renders a DEM as a tilted 3D slab floating on a white page: paper-white lowlands, deep
charcoal on shaded mountain faces, a pale receding sea, data draped as a translucent
colour ramp, a soft periwinkle cast shadow, and a serif title block. The look is
Milos Popovic's relief plates; the machinery is plain numpy.

## Start with the question, not the data

The most consequential decision is what the colour represents, and it is easy to get
wrong in a way that still produces a beautiful map. Before rendering, settle:

**"Where did these events originate?" and "where were people affected?" are different
maps from the same data — and often support opposite conclusions.**

An epicentre map shows where ruptures began. Effects propagate outward and decay with
distance, so a dense city far from any epicentre appears blank while having been
severely shaken. In Pakistan, the Punjab plain holds 61 epicentres against 840 in the
Sulaiman belt at the same latitudes — yet 98.4% of the country has felt MMI IV or more
since 2000, and Lahore's worst shaking came from a rupture 340 km away.

The same source-versus-receptor split recurs constantly: emissions inventory vs measured
concentration, dam release vs downstream inundation, case residence vs exposure site. Ask
which one the user actually means. If both matter, make both, and have each legend say
what it is **not**.

`references/pitfalls.md` covers this and the related traps — why intensity cannot be
converted to magnitude per location, why a long catalogue is partly a record of
instruments, why a maximum discards recurrence. Read it before designing the layer.

## Pipeline

Four stages. `scripts/make_map.py` is a working end-to-end example — copy it and edit,
it is the fastest route to a first frame.

```bash
python scripts/make_map.py --bbox 59.6 21.2 78.4 37.4 --title "PAKISTAN" --quakes --preview
```

**Always develop at preview resolution first.** A preview is seconds; a 4K frame plus
data fetch is minutes. Iterate on look, then render once at full size.

### 1. Terrain

```python
from terrain import fetch_dem, lonlat_to_grid
dem, cell_m = fetch_dem((lon0, lat0, lon1, lat1), width=4200, zoom=9, cache_dir="_cache")
```

Pulls AWS Terrain Tiles and resamples to an equal-ground-pixel grid. These tiles carry
**real bathymetry**, which is why coastal frames get a sea surface rather than a hole —
extend the bbox past the coastline when the sea should show. Tiles cache; only the first
run pays. Use `zoom=8` for previews, `9` for country-scale 4K.

### 2. Texture

```python
from shading import TerrainCanvas, point_density, RAMP_VIOLET
canvas = TerrainCanvas(dem, cell_m)
rows, cols = lonlat_to_grid(lons, lats, bbox, dem.shape)
field = point_density(rows, cols, dem.shape, weights=w, radius_px=r)
alpha = np.clip(field * 3.1, 0, 1) ** 0.72 * 0.97
tex = canvas.compose(layers=[(field, alpha, RAMP_VIOLET)], focus=inside_mask, border=line)
```

`compose` takes any number of `(field01, alpha01, ramp)` layers, so the same machinery
serves earthquakes, flood extent, population, rainfall — anything you can put on the
grid. Keep alpha well under 1: if the hillshade vanishes under the wash you have made a
choropleth, and the terrain was the point.

Boundary masks need `geopandas` + `rasterio`; they are optional, so skip them gracefully
if those are unavailable.

### 3. Oblique render

```python
from oblique import Camera, autoframe, render, compose
from shading import edge_falloff
taper = edge_falloff(*dem.shape)
cam = autoframe(dem, cell_m, out_w * 2, out_h * 2, Camera())
rgb, cov = render(dem, tex, cell_m, out_w * 2, out_h * 2, cam, edge_taper=taper)
img = compose(rgb, cov, out_w, out_h)          # downsamples ×2 and adds the cast shadow
```

Painter's-algorithm perspective rasteriser with true height displacement and occlusion.
4K in about three seconds on CPU.

**Always `autoframe()`.** Region aspect ratios vary enormously and hand-tuned zoom never
transfers; it fits the slab into a target rect that reserves the left quarter for type.

Before reaching for a GPU engine, verify it honours camera tilt — render the same scene
at 20° and 60° and diff. Libraries that silently render flat top-down, and path tracers
that fail on integrated GPUs, are both documented in `references/pitfalls.md`.

### 4. Page

```python
from oblique import draw_title, draw_legend, draw_credits
d = draw_title(img, ["Caption line one", "line two"], "SUBJECT", scale=scale)
draw_legend(d, x, y, scale, RAMP_VIOLET, "VARIABLE NAME", ticks, note)
draw_credits(d, ["Source: ...", "Elevation: ..."], out_w, out_h, scale)
```

**Every map gets a legend**, and the note must say what the colour *is* — and, when a
companion map could be confused with it, what it is not. A ramp without a stated variable
looks quantitative and readers supply their own meaning, usually the wrong one.

## Environment

Core path needs only **numpy, scipy, Pillow, requests**. `geopandas`/`rasterio` are
optional (boundaries), `duckdb` useful for analysing tabular catalogues.

Confirm which interpreter actually has the stack before running — on Windows `py` and
`python` are frequently different installs. Set `PYTHONIOENCODING=utf-8` when place
names may contain non-Latin-1 characters.

Works the same in Claude Code, Cowork, and claude.ai. In sandboxes without network
access, ask the user to supply a DEM (any GeoTIFF read into a numpy array works — pass
it straight to `TerrainCanvas`) since tile fetching will fail.

## Memory

A 4K grid is ~17M cells; one float32 RGB buffer is ~200 MB and naive expressions create
several at once. The bundled code composites **one colour channel at a time** to keep the
peak to a single `(h, w)` scratch. Check headroom before a big run:

```python
import psutil; print(psutil.virtual_memory().available / 2**30, "GB free")
```

If it is tight, reduce the working grid width rather than the output resolution — the
render downsamples anyway.

## Documenting the result

These maps usually want a companion technical report: data provenance with resolutions
and access dates, the software stack, the processing pipeline, an analysis of what the
map shows, and an explicit limitations section. State modelled-versus-measured, the time
window and what it excludes, and any clipping. If the report is academic, verify every
citation against its publisher rather than writing it from memory.

## Reference files

- `references/data-sources.md` — fetch recipes and gotchas for elevation/bathymetry,
  USGS catalogues, ShakeMap intensity grids, boundaries, and other thematic layers.
  Read when sourcing data.
- `references/cartography.md` — the measured palette, hillshade recipe, layer and camera
  parameters, page composition, legend rules. Read when tuning the look.
- `references/pitfalls.md` — analytical traps and rendering artefacts, each with its fix.
  Read before designing a layer, and when something looks wrong.

## Scripts

- `scripts/terrain.py` — `fetch_dem`, `grid_shape`, `lonlat_to_grid`
- `scripts/shading.py` — `TerrainCanvas`, `hillshade`, `edge_falloff`, `point_density`, ramps
- `scripts/oblique.py` — `Camera`, `autoframe`, `render`, `compose`, title/legend/credits
- `scripts/make_map.py` — runnable end-to-end example
