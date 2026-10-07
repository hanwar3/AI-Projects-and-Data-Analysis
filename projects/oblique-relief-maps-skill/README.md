# oblique-relief-maps: a Claude skill

A reusable skill that teaches Claude (Claude Code or claude.ai) to make publication-quality oblique 3D relief maps and the technical report that documents them.

I wrote it after the Pakistan earthquake maps, to capture what took the longest to get right: the cartographic recipe, the data sources that work, the rendering pitfalls, and the question that keeps these maps honest. With the skill installed, the Pakistan climate map and all three Texas maps were produced from a short request each, in a consistent style.

## What is in it

| File | Purpose |
|---|---|
| `SKILL.md` | When to use the skill and the workflow, starting with "what does the colour represent?" |
| `references/cartography.md` | The visual recipe: palette, light angles, type, legend and credit conventions |
| `references/data-sources.md` | DEMs, bathymetry, USGS catalogues and ShakeMap, climate grids, boundaries |
| `references/pitfalls.md` | Origin-versus-impact errors, catalogue completeness, projection and texture traps |
| `scripts/terrain.py` | DEM fetch and mosaic from AWS Terrain Tiles, lon/lat to grid |
| `scripts/shading.py` | Hillshade, tints, point-density layers, bivariate colour |
| `scripts/oblique.py` | Perspective renderer with height displacement and occlusion; title, legend and credit blocks |
| `scripts/make_map.py` | End-to-end example |

The renderer is plain NumPy and runs on a CPU, which mattered: the GPU path tracer I tried first (forge3d) failed on my laptop's integrated GPU.

## Install

Copy this folder to `~/.claude/skills/oblique-relief-maps/` for Claude Code, or upload it as a skill in claude.ai. Claude loads it whenever a request involves terrain, relief or hazard maps.
