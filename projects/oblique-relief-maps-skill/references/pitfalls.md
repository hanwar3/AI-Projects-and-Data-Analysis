# Pitfalls

Failure modes that cost real time, and the analytical traps that produce a beautiful
map making a false claim. The second category matters more.

## Contents
- [Analytical traps](#analytical-traps)
- [Rendering engines](#rendering-engines)
- [Rendering artefacts](#rendering-artefacts)
- [Memory](#memory)
- [Environment](#environment)

---

## Analytical traps

### Mapping where an event *started* and calling it impact

The single most consequential error. An epicentre is the surface point above where a
rupture initiated. Effects — shaking, flooding, ashfall, noise — propagate outward and
decay with distance. A dense city 300 km from any epicentre can be damaged while
appearing blank on an epicentre map.

Concretely: Pakistan's Punjab plain holds 61 catalogued epicentres against 840 in the
Sulaiman belt at the same latitudes, yet 98.4% of the country has felt at least MMI IV
since 2000. Lahore's worst shaking, MMI 5.5, came from an M7.6 rupture 340 km away.

**Before choosing a layer, ask the question the map must answer.** "Where do these
originate?" and "where are people affected?" are different maps from the same events,
and often point to opposite conclusions about who is at risk. When both matter, make
both and say in each legend what it is not.

### Intensity cannot be converted to magnitude per location

Magnitude is a property of the **source**: one scalar per event, proportional to the
log of seismic moment. It has no spatial variation — an M7.6 earthquake is M7.6
everywhere. Intensity is a property of a **place**.

So there is no such thing as "the magnitude at this location", and painting one on
every cell fabricates a field. The valid inverse (Bakun & Wentworth 1997, BSSA
87(6):1502–1521) consumes the *whole* intensity pattern plus distances and returns one
magnitude for the event — used for pre-instrumental earthquakes. It cannot return a
per-cell value.

No dataset holds "felt magnitude" either. USGS DYFI is genuine felt data contributed
by the public, but reports *Community Decimal Intensity* — an intensity — for exactly
this reason.

**What is well defined is attribution**: for each cell, which event produced its
maximum, and what that event's magnitude was. Track it while compositing; it answers
the intuitive question honestly.

The same distinction recurs elsewhere. Source-vs-receptor pairs to watch: emissions
inventory vs measured concentration; reservoir release vs downstream inundation;
transmitter location vs received signal; case residence vs exposure site.

### A long window is a record of instruments as much as of the world

Detection capability improves over time. In global seismic catalogues the smallest
recorded event drops abruptly around 1973. A 125-year map is then partly a map of when
and where instruments existed, and its densest clusters may mark the best-monitored
regions rather than the most active ones.

Check `MIN(value) GROUP BY year` for any long series before mapping it. Either restrict
to a homogeneous window or state the heterogeneity on the map. The same applies to
citizen-science observations, disease surveillance, and crime reporting.

### Maximum discards recurrence

A cell-wise maximum shows the worst single event. A place shaken once at MMI VI and one
shaken twenty times look identical. If frequency matters, map exceedance counts or a
rate alongside it, and say which you chose.

### A short window is a lower bound

25 years of ground motion excludes the great events that define long-run hazard. Label
such a map as a lower bound on credible severity, not a maximum-credible-event map, and
name the large historic events that fall outside the window.

## Rendering engines

Verify tilt before committing to a 3D library. Two failure modes seen in practice:

- A "3D map scene" API that silently ignores camera elevation and renders flat
  top-down. Test it: render the same scene at 20° and 60° and diff the images. If the
  mean difference is ~1/255, the camera is not wired up.
- A GPU path tracer that fails or crawls on integrated GPUs — one returned
  `ReSTIR reuse chain produced no valid reservoirs` on 4 of 4 attempts at ~60 s each,
  and needed >120 s at 768×512.

The bundled CPU rasteriser (`scripts/oblique.py`) renders 4K in ~3 s and does not
depend on any of this. Prefer it unless you specifically need physically based light
transport.

## Rendering artefacts

| Symptom | Cause | Fix |
|---|---|---|
| Dark hairline tracing the whole map | Render buffer initialised to black; downsampling blends edges toward it | Fill the buffer with the page colour |
| Terrain looks like fur or grass | Vertical exaggeration too high for the sampling | Drop to ~13 and smooth the height field before displacement |
| Straight cliff wall at the frame edge | Grid rectangle standing up at full height | Taper heights at the margins with the same mask that fades the texture |
| Rectangular seams inside a composited field | Union of finite source rectangles | Clip to a region of complete coverage, feathered |
| Bright vertical streaks below summits | Skirt fill painted with the summit's colour | Darken progressively down the skirt |
| Everything one flat colour | Alpha too high or normalisation at the max | Lower alpha; normalise at ~97.5th percentile |
| Caption unreadable over terrain | Camera framing not adapted to region aspect | `autoframe()` with a target rect reserving the type column |

## Memory

A 4K working grid is ~17M cells. One float32 `(h, w, 3)` buffer is ~200 MB, and naive
expressions create several temporaries at once — easily >1 GB, on a machine that may
have far less free.

**Composite one colour channel at a time.** Peak drops to a single `(h, w)` scratch.
Check headroom before a large run rather than after the crash:

```python
import psutil; print(psutil.virtual_memory().available / 2**30, "GB free")
```

Downloads deserve the same care: stream large per-event grids into a running
aggregate and cache only the aggregate.

## Environment

- **The interpreter that has the geospatial stack may not be the one on `PATH`.** On
  Windows, `py` often resolves to a different install than `python`. Verify with
  `python -c "import rasterio"` before assuming.
- **numpy 2.x removed `ndarray.ptp()`** — use `np.ptp(a)`.
- **`geopandas`/`rasterio` are heavy and may be absent** in sandboxes. The core
  pipeline needs only numpy, scipy, Pillow, requests; keep boundary handling optional
  and degrade gracefully.
- **Console encoding**: on Windows `cp1252` will crash on place names containing
  non-Latin-1 characters. Set `PYTHONIOENCODING=utf-8`.
- **Fonts differ by platform.** Probe a list and fall back to `load_default()` rather
  than hard-coding one family.
