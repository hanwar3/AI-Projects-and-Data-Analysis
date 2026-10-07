# Cartographic recipe

The visual system behind the white-canvas oblique relief look, and why each part is
tuned the way it is. Values are the defaults in `scripts/shading.py` and
`scripts/oblique.py`; deviate deliberately, not accidentally.

## Contents
- [The palette](#the-palette)
- [Relief shading](#relief-shading)
- [The sea](#the-sea)
- [Data layers](#data-layers)
- [Geometry and camera](#geometry-and-camera)
- [Page composition](#page-composition)
- [Legends](#legends)

---

## The palette

Sampled from reference plates rather than invented, which is why it holds together:

| Role | RGB | Hex |
|---|---|---|
| Page / lit ground / snow | 255, 255, 255 | `#FFFFFF` |
| Silver mid-slopes | 145, 150, 161 | `#919AA1` |
| Charcoal shadow faces | 39, 39, 53 | `#272735` |
| Sea, shallow | 223, 233, 247 | `#DFE9F7` |
| Sea, deep | 178, 198, 228 | `#B2C6E4` |
| Cast shadow (periwinkle) | 186, 201, 228 | `#BAC9E4` |
| Title ink | 17, 17, 20 | `#111114` |
| Subject accent | 74, 80, 195 | `#4A50C3` |
| Credits | 78, 78, 88 | `#4E4E58` |

The shadow colour is slightly **blue**, not neutral grey. That cool cast is most of why the relief reads as lit by daylight rather than dirty.

If you need a palette for a different subject, sample a reference image directly rather than guessing — take percentiles of the pixels belonging to each element. Guessing lands "close" and looks wrong.

## Relief shading

**Multi-directional hillshade.** One light direction leaves slopes facing it featureless. Combine three:

| Weight | Azimuth | Altitude | Purpose |
|---|---|---|---|
| 0.56 | 315° | 38° | principal NW light (the cartographic convention) |
| 0.22 | 270° | 58° | fill, lifts west-facing slopes |
| 0.22 | 15° | 34° | counter-light, sharpens ridge crests |

**Scale the z-factor by cell size.** Gradients are `dz/cell`, so the same z-factor gives different contrast at different resolutions and your preview stops predicting the final. Quote it at a reference resolution and scale: `zf = zf_ref * (cell_m / 350)`.

**Gate contrast by local relief.** Applying hillshade uniformly makes flat ground look dirty. Compute local ruggedness as the residual from a ~6 km Gaussian, normalise it, and multiply the darkening by it. Plains stay paper-white; mountains take the full range. This single step is the difference between "satellite screenshot" and the intended look.

**Add a valley term.** Hillshade alone renders ranges as bright ground scratched with thin dark lines. A crude ambient-occlusion term — how far below the smoothed surface a cell sits — gives whole ranges tonal mass:

```
valley = clip((gaussian(dem, 12km) - dem) / 170, 0, 1)
dev    = clip(dev + 0.40 * valley * relief, 0, 1)
```

**Tone ramp** is two segments: white → silver over the first 42% of the darkening range, silver → charcoal over the rest. Weighting the bright half short keeps most of the map light.

**Snow** brightens lit faces above ~4,200 m. Skip it below that and in arid ranges.

## The sea

Render sea as a **flat plane at zero elevation** (`z = max(dem, 0)`) with colour driven by bathymetric depth. A receding flat sea in the near field is a strong depth cue and instantly reads as ocean. Feather the coastline by ~1 px so it does not alias into a jagged staircase.

## Data layers

Layers are `(field01, alpha01, ramp)` composited over the relief.

- **Normalise at a high percentile (~97.5), not the maximum.** One dense cluster otherwise flattens everything else to invisibility.
- **Keep alpha well below 1** (0.6–0.7 for continuous fields). If the hillshade disappears under the wash, you have replaced a terrain map with a choropleth, and the terrain was the point.
- **Point data**: scale kernel radius *and* amplitude with the point's weight; accumulate additively so clusters saturate naturally.
- **Continuous fields**: set the alpha floor so values below the "interesting" threshold stay transparent. Everything coloured makes nothing legible.
- **Ramps**: `RAMP_VIOLET`, `RAMP_EMBER`, `RAMP_TEAL` in `shading.py`. Violet holds up against silver relief and stays distinguishable under red-green colour vision deficiency; ember reads as heat/fire; teal as water.

**Clip a layer to its region of validity.** If coverage is genuinely complete only inside some boundary, clip there with a feathered edge and say so — otherwise the union of finite source rectangles shows as hard seams that look like data.

## Geometry and camera

**Vertical exaggeration should be modest — around ×13.** The drama comes from the hillshade. Push much past ~18 and summits resolve into needle spikes; the terrain reads as fur.

**Smooth the height field before displacement, not the texture.** ~2.6 km of Gaussian on the geometry removes single-pixel spikes while the texture keeps full detail. Sharp relief, clean silhouette.

**Taper heights at the grid margins.** Otherwise the working rectangle stands up as a cliff wall at its edges. Taper geometry and fade texture to white with the *same* mask so they agree.

**Pitch around 50°** keeps the plan shape readable while still reading as 3D. Lower is more dramatic and occludes more; below ~30° the far half disappears behind the near half.

**Always autoframe.** Region aspect ratios vary enormously and hand-tuned zoom/pan never transfers. `autoframe()` fits the projected slab into a target rectangle that reserves the left quarter for type.

## Page composition

**Supersample ×2 and Lanczos down.** The rasteriser produces hard silhouette edges; this is what makes them clean.

**Initialise the render buffer to the page colour, not black.** Downsampling blends silhouette pixels against whatever the buffer held; black leaves a dark hairline tracing the whole map. This bug looks like a deliberate outline and is easy to miss.

**Cast shadow**: accumulate ~26 offset copies of the coverage mask along the light direction with decaying weight, blur, tint periwinkle, and composite *under* the terrain, masked out where terrain covers. The streaky directional quality — not a uniform blur — is what sells the floating slab.

**Typography**: a serif family (Georgia, Cambria, DejaVu Serif). Caption lines in ink at ~58 px, the subject in accent colour at ~156 px, credits at ~25 px (quoted at 3840 px wide; scale linearly). Keep it all in one left-hand column.

## Legends

**A ramp without a stated variable is the most common way these maps mislead.** The colour looks quantitative and readers supply their own meaning — usually the wrong one.

Every legend needs: the variable name, tick labels with units, and a plain-language note saying what the colour *is* and, where it matters, what it is **not**. If two related maps could be confused (origin vs impact, count vs rate, stock vs flow), say so explicitly in the note and point to the companion map.
