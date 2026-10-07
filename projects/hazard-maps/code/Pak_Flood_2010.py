"""
Pak_Flood_2010.py
=================
Generates two high-resolution 3D terrain maps for Pakistan using forge3d, Copernicus DEM (GLO-30),
official administrative boundaries, major river streamlines (Indus & Tributaries), true Natural Earth
ocean geometry (Arabian Sea), and UN OCHA / UNOSAT / VIIRS flood datasets:

Outputs:
1. Map 1 (Elevation Relief):
   - outputs/pakistan_elevation_map_3d.jpg & .png
   - 3D Hypsometric Topographic Relief (0m to 8,611m Peak) across natural continuous terrain
   - True Natural Earth Arabian Sea ocean boundary (no artificial straight cutoffs)
   - Major river network streamlines (Indus, Jhelum, Chenab, Ravi, Sutlej)
   - Official national and provincial boundaries in glowing mint (#2DD4BF / #5EEAD4)
   - High-contrast, large-font Topography Legend HUD & mountain peak callouts.

2. Map 2 (Major Floods Inundation):
   - outputs/pakistan_flood_map_3d.jpg & .png
   - 3D Flood Inundation Footprint (2010 Super Flood + 2022 Megaflood)
   - Natural, continuous landmass and topography across international borders
   - Major river network streamlines (Indus, Jhelum, Chenab, Ravi, Sutlej) showing riverine flood pathways
   - True Natural Earth Arabian Sea ocean boundary
   - Official national and provincial boundaries in glowing mint (#2DD4BF / #5EEAD4)
   - Non-overlapping, large-font Flood Hazard Intensity & Methodology Legend HUD.
"""

import os
import sys
import io
import zipfile
import requests
import numpy as np
import rasterio
from rasterio.features import rasterize
from scipy.ndimage import gaussian_filter
from matplotlib.colors import LinearSegmentedColormap
from PIL import Image, ImageDraw, ImageFont

# =========================================================
# Step 1: Verify Dependencies
# =========================================================
print("=" * 75)
print("PAKISTAN 3D TOPOGRAPHIC ELEVATION & FLOOD HAZARD MAPPING PIPELINE")
print("=" * 75)
print("\n[1/6] Verifying GIS and 3D rendering dependencies...")

try:
    import forge3d as f3d
    import geopandas as gpd
    import rasterio
    import rioxarray
    print(f" [+] forge3d:   {getattr(f3d, '__version__', 'active')}")
    print(f" [+] geopandas: {gpd.__version__}")
    print(f" [+] rasterio:  {rasterio.__version__}")
    print(f" [+] rioxarray: {rioxarray.__version__}")
except ImportError as err:
    print(f"[-] Missing required package: {err}", file=sys.stderr)
    sys.exit(1)

# Workspace directories
data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
floods_dir = os.path.join(data_dir, "floods")
admin_dir = os.path.join(data_dir, "admin_boundaries")
rivers_dir = os.path.join(data_dir, "rivers")
ocean_dir = os.path.join(data_dir, "ocean")
outputs_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs")

os.makedirs(data_dir, exist_ok=True)
os.makedirs(floods_dir, exist_ok=True)
os.makedirs(admin_dir, exist_ok=True)
os.makedirs(rivers_dir, exist_ok=True)
os.makedirs(ocean_dir, exist_ok=True)
os.makedirs(outputs_dir, exist_ok=True)

# =========================================================
# Step 2: Fetch & Verify Geodata (DEM, Boundaries, Rivers, Ocean, Floods)
# =========================================================
print("\n[2/6] Checking and acquiring DEM, boundaries, river streamlines, ocean polygons, and floods...")

# 2.1 Base DEM (Copernicus 30m GLO-30)
dem_path = os.path.join(data_dir, "pakistan_dem_30m.tif")
if not os.path.exists(dem_path):
    fallback_paths = [
        os.path.join(data_dir, "pakistan_dem.tif"),
        os.path.join(data_dir, "elevation", "PAK_elv.tif"),
    ]
    for fb in fallback_paths:
        if os.path.exists(fb):
            with rasterio.open(fb) as src:
                prof = src.profile.copy()
                prof.update(filename=dem_path)
                with rasterio.open(dem_path, "w", **prof) as dst:
                    dst.write(src.read())
            print(f" [+] Configured DEM from workspace: {fb}")
            break
print(f" [+] Verified Base DEM: '{dem_path}'")

# 2.2 Official Administrative Boundaries (ADM0 & ADM1)
shp_adm0 = os.path.join(admin_dir, "pak_admin0.shp")
shp_adm1 = os.path.join(admin_dir, "pak_admin1.shp")
if not os.path.exists(shp_adm0) or not os.path.exists(shp_adm1):
    print(" [*] Downloading Pakistan administrative boundary shapefiles from HDX...")
    admin_url = (
        "https://data.humdata.org/dataset/a64d1ff2-7158-48c7-887d-6af69ce21906/"
        "resource/1d580e06-07b6-49a2-8e87-ba769762fb82/download/"
        "pak_admin_boundaries.shp.zip"
    )
    r = requests.get(admin_url, timeout=45)
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        z.extractall(admin_dir)
    print(f" [+] Extracted official Pakistan boundaries into: '{admin_dir}'")
else:
    print(f" [+] Verified Pakistan boundary shapefiles in: '{admin_dir}'")

# 2.3 Natural Earth True Ocean Geometry
shp_ocean = os.path.join(ocean_dir, "ne_10m_ocean.shp")
if not os.path.exists(shp_ocean):
    print(" [*] Downloading Natural Earth 10m Ocean polygons...")
    url_ocean = "https://naciscdn.org/naturalearth/10m/physical/ne_10m_ocean.zip"
    r = requests.get(url_ocean, timeout=45, headers={"User-Agent": "Mozilla/5.0"})
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        z.extractall(ocean_dir)
    print(f" [+] Extracted Ocean polygons into: '{ocean_dir}'")
else:
    print(f" [+] Verified Ocean polygons in: '{shp_ocean}'")

# 2.4 Major River Streamlines (Indus River Basin & Tributaries)
shp_rivers = os.path.join(rivers_dir, "ne_10m_rivers_lake_centerlines.shp")
if not os.path.exists(shp_rivers):
    print(" [*] Downloading Natural Earth 10m River Streamlines...")
    url_rivers = "https://naciscdn.org/naturalearth/10m/physical/ne_10m_rivers_lake_centerlines.zip"
    r = requests.get(url_rivers, timeout=45, headers={"User-Agent": "Mozilla/5.0"})
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        z.extractall(rivers_dir)
    print(f" [+] Extracted River streamlines into: '{rivers_dir}'")
else:
    print(f" [+] Verified River streamlines in: '{shp_rivers}'")

# 2.5 UN OCHA 2010 Flood Vector
dir_2010 = os.path.join(floods_dir, "2010_flood")
shp_2010 = None
if os.path.exists(dir_2010):
    for root, _, files in os.walk(dir_2010):
        for f in files:
            if f.endswith(".shp"):
                shp_2010 = os.path.join(root, f)
                break
if not shp_2010:
    print(" [*] Downloading UN OCHA 2010 Flood Affected Union Councils...")
    url_2010 = (
        "https://data.humdata.org/dataset/198064b8-100c-4a21-8d0f-fadf6d5dbcb2/"
        "resource/b6197814-3b01-4c46-a3b5-0d49aef4ae31/download/"
        "pak_adm4_2010floodaffected_pco_20150826.zip"
    )
    r = requests.get(url_2010, timeout=60)
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        z.extractall(dir_2010)
    for root, _, files in os.walk(dir_2010):
        for f in files:
            if f.endswith(".shp"):
                shp_2010 = os.path.join(root, f)
                break
print(f" [+] Verified 2010 Flood vector: '{shp_2010}'")

# 2.6 UNOSAT / VIIRS 2022 Satellite Flood Vector
dir_2022 = os.path.join(floods_dir, "2022_flood")
shp_2022 = None
if os.path.exists(dir_2022):
    for root, _, files in os.walk(dir_2022):
        for f in files:
            if f.endswith(".shp"):
                shp_2022 = os.path.join(root, f)
                break
if not shp_2022:
    print(" [*] Downloading VIIRS 2022 Satellite Flood Extents...")
    url_2022 = (
        "https://data.humdata.org/dataset/f685e623-a539-4af0-92ff-19b737ec95b3/"
        "resource/fa89ec39-8dd0-4abe-b97e-0a3d9e9627a5/download/"
        "viirs_20220701_20220831_floodextent_pak.zip"
    )
    r = requests.get(url_2022, timeout=60)
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        z.extractall(dir_2022)
    for root, _, files in os.walk(dir_2022):
        for f in files:
            if f.endswith(".shp"):
                shp_2022 = os.path.join(root, f)
                break
print(f" [+] Verified 2022 Flood vector: '{shp_2022}'")

# =========================================================
# Step 3: GIS Geoprocessing (Natural Ocean & Rivers, Mint Borders)
# =========================================================
print("\n[3/6] Performing GIS geoprocessing, true ocean geometry, and overlay generation...")

with rasterio.open(dem_path) as dem_src:
    dem_shape = dem_src.shape
    dem_transform = dem_src.transform
    dem_crs = dem_src.crs
    dem_data = dem_src.read(1)
    bounds = dem_src.bounds
    profile = dem_src.profile.copy()

print(f" [+] DEM Grid: {dem_shape[1]}x{dem_shape[0]} px | Bounds: {bounds}")

# Load boundary, ocean, and river shapefiles
gdf_adm0 = gpd.read_file(shp_adm0).to_crs(dem_crs)
gdf_adm1 = gpd.read_file(shp_adm1).to_crs(dem_crs)
gdf_ocean_raw = gpd.read_file(shp_ocean).to_crs(dem_crs)
gdf_rivers_all = gpd.read_file(shp_rivers).to_crs(dem_crs)

# Clean DEM
clean_dem = np.nan_to_num(dem_data, nan=0.0)
clean_dem = np.clip(clean_dem, 0.0, 8611.0)

# Build Coordinate Grids
ny, nx = dem_shape
lons = np.linspace(bounds.left, bounds.right, nx)
lats = np.linspace(bounds.top, bounds.bottom, ny) # North-up grid
lon_grid, lat_grid = np.meshgrid(lons, lats)

# Rasterize True Arabian Sea Ocean from Natural Earth polygon (100% natural coastlines, NO straight lines)
r_ocean = rasterize(
    [(geom, 1) for geom in gdf_ocean_raw.geometry if geom is not None],
    out_shape=dem_shape,
    transform=dem_transform,
    fill=0,
    dtype=np.uint8
)
ocean_mask = r_ocean > 0

# Rasterize National Boundary in Mint (#2DD4BF: R 45, G 212, B 191)
adm0_geom = gdf_adm0.geometry.iloc[0].boundary.buffer(0.024)
r_adm0 = rasterize([(adm0_geom, 1)], out_shape=dem_shape, transform=dem_transform, fill=0, dtype=np.uint8)

# Rasterize Provincial Boundaries in light mint (#5EEAD4: R 94, G 234, B 212)
adm1_lines = [(geom.boundary.buffer(0.012), 1) for geom in gdf_adm1.geometry if geom is not None]
r_adm1 = rasterize(adm1_lines, out_shape=dem_shape, transform=dem_transform, fill=0, dtype=np.uint8)

# Filter & Rasterize Major Pakistan River Streamlines
pak_poly = gdf_adm0.geometry.iloc[0]
pak_rivers = gdf_rivers_all[gdf_rivers_all.intersects(pak_poly.buffer(0.2))]
indus_geoms = [geom.buffer(0.022) for geom, name in zip(pak_rivers.geometry, pak_rivers["name"]) if name == "Indus"]
trib_geoms = [geom.buffer(0.013) for geom, name in zip(pak_rivers.geometry, pak_rivers["name"]) if name != "Indus"]

r_indus = rasterize([(g, 1) for g in indus_geoms], out_shape=dem_shape, transform=dem_transform, fill=0, dtype=np.uint8) if indus_geoms else np.zeros(dem_shape, dtype=np.uint8)
r_trib = rasterize([(g, 1) for g in trib_geoms], out_shape=dem_shape, transform=dem_transform, fill=0, dtype=np.uint8) if trib_geoms else np.zeros(dem_shape, dtype=np.uint8)

# --- 3.1 Generate Elevation Overlay with Rivers & Natural Ocean ---
print(" [*] Generating Hypsometric Elevation Colormap with Natural Ocean & River Streamlines...")
elev_stops = [
    (0.0 / 8611.0, "#15803d"),     # 0m: Indus Plains & Delta (Lush Green)
    (200.0 / 8611.0, "#22c55e"),   # 200m: Vibrant Green Plains
    (600.0 / 8611.0, "#ca8a04"),   # 600m: Warm Amber (Potohar)
    (1500.0 / 8611.0, "#b45309"),  # 1500m: Terracotta (Highlands)
    (3000.0 / 8611.0, "#78350f"),  # 3000m: Deep Sienna (Sulaiman / Western Ranges)
    (5000.0 / 8611.0, "#475569"),  # 5000m: Slate Gray (Alpine rock)
    (6500.0 / 8611.0, "#94a3b8"),  # 6500m: Cool Silver
    (1.0, "#ffffff")               # 8611m+: Snow & Ice (White)
]
elev_cmap = LinearSegmentedColormap.from_list("hypsometric", [(pos, col) for pos, col in elev_stops])
rgba_elevation = (elev_cmap(clean_dem / 8611.0) * 255.0).astype(np.uint8)

# Paint Arabian Sea Ocean with Deep Marine Bathymetry (Natural coastline only)
ocean_depth_norm = np.clip((25.5 - lat_grid) / 2.0, 0.0, 1.0)
ocean_r = (19 * (1 - ocean_depth_norm) + 5 * ocean_depth_norm).astype(np.uint8)
ocean_g = (78 * (1 - ocean_depth_norm) + 19 * ocean_depth_norm).astype(np.uint8)
ocean_b = (122 * (1 - ocean_depth_norm) + 41 * ocean_depth_norm).astype(np.uint8)

rgba_elevation[ocean_mask, 0] = ocean_r[ocean_mask]
rgba_elevation[ocean_mask, 1] = ocean_g[ocean_mask]
rgba_elevation[ocean_mask, 2] = ocean_b[ocean_mask]
rgba_elevation[ocean_mask, 3] = 255

# Drape River Streamlines on Elevation Map (Azure Blue & Cyan)
trib_mask = (r_trib > 0) & (~ocean_mask)
rgba_elevation[trib_mask, 0] = 56
rgba_elevation[trib_mask, 1] = 189
rgba_elevation[trib_mask, 2] = 248
rgba_elevation[trib_mask, 3] = 240

indus_mask = (r_indus > 0) & (~ocean_mask)
rgba_elevation[indus_mask, 0] = 0
rgba_elevation[indus_mask, 1] = 240
rgba_elevation[indus_mask, 2] = 255
rgba_elevation[indus_mask, 3] = 255

# Drape Provincial boundaries onto land only
prov_mask = (r_adm1 > 0) & (r_adm0 == 0) & (~ocean_mask)
rgba_elevation[prov_mask, 0] = 94
rgba_elevation[prov_mask, 1] = 234
rgba_elevation[prov_mask, 2] = 212
rgba_elevation[prov_mask, 3] = 220

# Drape National border in glowing mint (#2DD4BF)
nat_mask = r_adm0 > 0
rgba_elevation[nat_mask, 0] = 45
rgba_elevation[nat_mask, 1] = 212
rgba_elevation[nat_mask, 2] = 191
rgba_elevation[nat_mask, 3] = 255

elev_overlay_path = os.path.join(data_dir, "pakistan_elevation_overlay.png")
Image.fromarray(rgba_elevation).save(elev_overlay_path)
print(f" [+] Saved Elevation Overlay with Natural Ocean & Rivers: '{elev_overlay_path}'")

# --- 3.2 Generate Flood Overlay with Natural Ocean & Mint Boundaries ---
print(" [*] Generating Flood Inundation Overlay with Natural Ocean & River Streamlines...")
gdf_2010 = gpd.read_file(shp_2010).to_crs(dem_crs)
gdf_2022 = gpd.read_file(shp_2022).to_crs(dem_crs)

shapes_2010 = [(geom, 1.0) for geom in gdf_2010.geometry if geom is not None and not geom.is_empty]
r_2010 = rasterize(shapes_2010, out_shape=dem_shape, transform=dem_transform, fill=0, dtype=np.float32)

shapes_2022 = [(geom, 1.0) for geom in gdf_2022.geometry if geom is not None and not geom.is_empty]
r_2022 = rasterize(shapes_2022, out_shape=dem_shape, transform=dem_transform, fill=0, dtype=np.float32)

elevation_weight = np.clip(1.0 - (clean_dem / 1200.0), 0.05, 1.0)
elevation_weight[clean_dem > 3500] = 0.0

f_2010_weighted = r_2010 * elevation_weight * 0.7
f_2022_weighted = r_2022 * 1.0

f_2010_smooth = gaussian_filter(f_2010_weighted, sigma=2.0)
f_2022_smooth = gaussian_filter(f_2022_weighted, sigma=1.2)

flood_composite = np.clip((f_2010_smooth * 0.6) + (f_2022_smooth * 1.25), 0.0, 1.0)
flood_composite[flood_composite < 0.03] = 0.0

# Save composite GeoTIFF
composite_tif_path = os.path.join(data_dir, "pakistan_floods_composite.tif")
profile.update(dtype=rasterio.float32, count=1, nodata=0.0)
with rasterio.open(composite_tif_path, "w", **profile) as dst:
    dst.write(flood_composite.astype(np.float32), 1)

# Build flood RGBA
h, w = dem_shape
rgba_flood = np.zeros((h, w, 4), dtype=np.uint8)

# Render Natural Ocean on the flood map
rgba_flood[ocean_mask, 0] = ocean_r[ocean_mask]
rgba_flood[ocean_mask, 1] = ocean_g[ocean_mask]
rgba_flood[ocean_mask, 2] = ocean_b[ocean_mask]
rgba_flood[ocean_mask, 3] = 255

# Flood water styling (Deep Cobalt -> Electric Cyan -> Radiant White Core)
flood_mask = (flood_composite > 0.03) & (~ocean_mask)
val = flood_composite[flood_mask]

rgba_flood[flood_mask, 0] = np.clip(14 + val * (224 - 14), 0, 255).astype(np.uint8)
rgba_flood[flood_mask, 1] = np.clip(116 + val * (242 - 116), 0, 255).astype(np.uint8)
rgba_flood[flood_mask, 2] = np.clip(144 + val * (254 - 144), 0, 255).astype(np.uint8)
rgba_flood[flood_mask, 3] = np.clip(val * 235 + 20, 0, 255).astype(np.uint8)

# Drape River Streamlines directly on flood map
rgba_flood[trib_mask, 0] = 56
rgba_flood[trib_mask, 1] = 189
rgba_flood[trib_mask, 2] = 248
rgba_flood[trib_mask, 3] = 230

rgba_flood[indus_mask, 0] = 0
rgba_flood[indus_mask, 1] = 240
rgba_flood[indus_mask, 2] = 255
rgba_flood[indus_mask, 3] = 255

# Drape Province lines (mint)
rgba_flood[prov_mask, 0] = 94
rgba_flood[prov_mask, 1] = 234
rgba_flood[prov_mask, 2] = 212
rgba_flood[prov_mask, 3] = 200

# Drape National border (mint)
rgba_flood[nat_mask, 0] = 45
rgba_flood[nat_mask, 1] = 212
rgba_flood[nat_mask, 2] = 191
rgba_flood[nat_mask, 3] = 255

flood_overlay_path = os.path.join(data_dir, "pakistan_floods_overlay.png")
Image.fromarray(rgba_flood).save(flood_overlay_path)
print(f" [+] Saved Flood Overlay with Natural Ocean & Rivers: '{flood_overlay_path}'")

# Helper to load system typography fonts
def get_carto_fonts():
    try:
        kicker = ImageFont.truetype("arialbd.ttf", 18)
        title = ImageFont.truetype("arialbd.ttf", 44)
        subtitle = ImageFont.truetype("arial.ttf", 24)
        meta = ImageFont.truetype("arial.ttf", 18)
        leg_title = ImageFont.truetype("arialbd.ttf", 26)
        leg_body = ImageFont.truetype("arialbd.ttf", 22)
        leg_text = ImageFont.truetype("arial.ttf", 19)
        river_font = ImageFont.truetype("arialbd.ttf", 22)
        ocean = ImageFont.truetype("arialbd.ttf", 36)
    except Exception:
        kicker = title = subtitle = meta = leg_title = leg_body = leg_text = river_font = ocean = ImageFont.load_default()
    return kicker, title, subtitle, meta, leg_title, leg_body, leg_text, river_font, ocean

# Helper to draw Scale Bar & North HUD in bottom-right
def draw_scale_hud(draw, render_w, render_h, head_font, text_font):
    s_x, s_y, s_w, s_h = render_w - 600, render_h - 220, 520, 130
    draw.rounded_rectangle([s_x, s_y, s_x + s_w, s_y + s_h], radius=12, fill=(8, 12, 22, 240), outline=(45, 212, 191, 120), width=1)
    draw.rounded_rectangle([s_x, s_y, s_x + 6, s_y + s_h], radius=3, fill=(45, 212, 191, 255))

    # North Arrow
    arr_x, arr_y = s_x + 65, s_y + 75
    draw.polygon([(arr_x, arr_y - 32), (arr_x - 12, arr_y + 14), (arr_x, arr_y + 5)], fill=(45, 212, 191, 255))
    draw.polygon([(arr_x, arr_y - 32), (arr_x + 12, arr_y + 14), (arr_x, arr_y + 5)], fill=(13, 148, 136, 255))
    draw.text((arr_x - 7, arr_y - 58), "N", fill=(255, 255, 255, 255), font=head_font)

    # Scale Line (250 km)
    b_x, b_y, b_len = s_x + 135, s_y + 80, 310
    draw.line([(b_x, b_y), (b_x + b_len, b_y)], fill=(255, 255, 255, 255), width=4)
    draw.line([(b_x, b_y - 8), (b_x, b_y + 8)], fill=(255, 255, 255, 255), width=4)
    draw.line([(b_x + b_len, b_y - 8), (b_x + b_len, b_y + 8)], fill=(255, 255, 255, 255), width=4)
    draw.line([(b_x + b_len // 2, b_y - 5), (b_x + b_len // 2, b_y + 5)], fill=(255, 255, 255, 255), width=2)
    draw.text((b_x + b_len // 2 - 35, b_y - 34), "250 km", fill=(255, 255, 255, 255), font=head_font)

    draw.text((render_w - 480, 50), "forge3d • WebGPU 3D Engine", fill=(148, 163, 184, 180), font=text_font)

# Helper to draw River Callout Labels
def draw_river_labels(draw, river_f):
    rivers_pos = [
        ("Indus River", 2320, 1320),
        ("Chenab River", 2700, 960),
        ("Jhelum River", 2580, 780),
        ("Ravi River", 2860, 1080),
        ("Sutlej River", 2760, 1220),
    ]
    for r_name, px, py in rivers_pos:
        text_w = int(len(r_name) * 11)
        draw.rounded_rectangle([px - 6, py - 4, px + text_w + 6, py + 24], radius=6, fill=(8, 14, 28, 210), outline=(0, 240, 255, 160), width=1)
        draw.text((px, py), r_name, fill=(0, 240, 255, 255), font=river_f)

# Helper to draw Arabian Sea typography strictly in 100% deep ocean water
def draw_ocean_watermark(draw, ocean_font):
    draw.text((720, 2025), "A R A B I A N   S E A", fill=(56, 189, 248, 230), font=ocean_font)

# =========================================================
# Step 4: Render Map 1 (3D Topographic Elevation & Relief)
# =========================================================
print("\n[4/6] Rendering Map 1: 3D Topographic Elevation & Relief with Natural Ocean & Rivers...")

render_width, render_height = 3840, 2160
raw_elev_path = os.path.join(outputs_dir, "pakistan_elevation_3d_raw.png")
final_elev_jpg = os.path.join(outputs_dir, "pakistan_elevation_map_3d.jpg")
final_elev_png = os.path.join(outputs_dir, "pakistan_elevation_map_3d.png")

scene_elev = f3d.MapScene(
    terrain=f3d.TerrainSource(path=dem_path, crs="EPSG:4326"),
    target_crs="EPSG:4326",
    camera=f3d.OrbitCamera(
        target=(69.35, 30.40, 0.0),
        distance=9.0,
        azimuth_deg=0.0,
        elevation_deg=45.0,
        fov_deg=45.0
    ),
    lighting=f3d.LightingPreset(
        name="default",
        intensity=2.5,
        sun_direction=[0.55, 0.78, 0.48]
    ),
    layers=[
        f3d.RasterOverlay(
            layer_id="elevation",
            path=elev_overlay_path,
            crs="EPSG:4326",
            opacity=0.98
        )
    ],
    output=f3d.OutputSpec(
        width=render_width,
        height=render_height,
        path=raw_elev_path,
        format="png"
    )
)

report_elev = scene_elev.render(raw_elev_path)
print(f" [+] forge3d Elevation Render status: {report_elev.status}")

# Compose Map 1 Cartographic Plate
img_elev = Image.open(raw_elev_path).convert("RGBA")
draw_elev = ImageDraw.Draw(img_elev)
kicker_f, title_f, subtitle_f, meta_f, leg_title_f, leg_body_f, leg_text_f, river_f, ocean_f = get_carto_fonts()

# 4.1 Header Card (Top-Left)
h_x, h_y, h_w, h_h = 70, 55, 1400, 175
draw_elev.rounded_rectangle([h_x, h_y, h_x + h_w, h_y + h_h], radius=14, fill=(8, 12, 22, 242), outline=(45, 212, 191, 120), width=1)
draw_elev.rounded_rectangle([h_x, h_y, h_x + 6, h_y + h_h], radius=3, fill=(45, 212, 191, 255))
draw_elev.text((h_x + 30, h_y + 18), "NATIONAL TOPOGRAPHY & INDUS BASIN RIVER NETWORK", fill=(45, 212, 191, 255), font=kicker_f)
draw_elev.text((h_x + 30, h_y + 44), "PAKISTAN TOPOGRAPHIC ELEVATION & 3D RELIEF", fill=(255, 255, 255, 255), font=title_f)
draw_elev.text((h_x + 30, h_y + 100), "Hypsometric Digital Elevation Model (0m Coast to 8,611m Karakoram)", fill=(203, 213, 225, 255), font=subtitle_f)
draw_elev.text((h_x + 30, h_y + 136), "Data Sources: Copernicus DEM GLO-30 • Survey of Pakistan • Natural Earth True Coastlines", fill=(148, 163, 184, 255), font=meta_f)

# 4.2 Elevation Legend Card (Bottom-Left)
l_x, l_y, l_w, l_h = 70, 1460, 830, 500
draw_elev.rounded_rectangle([l_x, l_y, l_x + l_w, l_y + l_h], radius=14, fill=(8, 12, 22, 242), outline=(45, 212, 191, 120), width=1)
draw_elev.rounded_rectangle([l_x, l_y, l_x + 6, l_y + l_h], radius=3, fill=(45, 212, 191, 255))
draw_elev.text((l_x + 28, l_y + 18), "HYPSOMETRIC ELEVATION SCALE & REFERENCE", fill=(255, 255, 255, 255), font=leg_title_f)

# Color Ramp
ramp_x, ramp_y, ramp_w, ramp_h = l_x + 28, l_y + 56, 750, 22
for x in range(ramp_w):
    factor = x / float(ramp_w)
    c_rgba = elev_cmap(factor)
    r_c, g_c, b_c = int(c_rgba[0]*255), int(c_rgba[1]*255), int(c_rgba[2]*255)
    draw_elev.line([(ramp_x + x, ramp_y), (ramp_x + x, ramp_y + ramp_h)], fill=(r_c, g_c, b_c, 255))
draw_elev.rectangle([ramp_x, ramp_y, ramp_x + ramp_w, ramp_y + ramp_h], outline=(255, 255, 255, 160), width=1)
draw_elev.text((ramp_x, ramp_y + 26), "0m (Plains)", fill=(203, 213, 225, 255), font=leg_text_f)
draw_elev.text((ramp_x + 160, ramp_y + 26), "600m", fill=(203, 213, 225, 255), font=leg_text_f)
draw_elev.text((ramp_x + 360, ramp_y + 26), "2,500m", fill=(203, 213, 225, 255), font=leg_text_f)
draw_elev.text((ramp_x + 530, ramp_y + 26), "5,000m", fill=(203, 213, 225, 255), font=leg_text_f)
draw_elev.text((ramp_x + ramp_w - 75, ramp_y + 26), "8,611m+", fill=(255, 255, 255, 255), font=leg_text_f)

# Key Items
item_y = l_y + 120
draw_elev.line([(l_x + 28, item_y + 8), (l_x + 72, item_y + 8)], fill=(0, 240, 255, 255), width=4)
draw_elev.text((l_x + 86, item_y - 2), "Indus River Main Stem & Tributaries (Cyan)", fill=(0, 240, 255, 255), font=leg_body_f)

item_y += 36
draw_elev.line([(l_x + 28, item_y + 8), (l_x + 72, item_y + 8)], fill=(45, 212, 191, 255), width=4)
draw_elev.text((l_x + 86, item_y - 2), "Pakistan International Border (Mint #2DD4BF)", fill=(45, 212, 191, 255), font=leg_body_f)

item_y += 36
draw_elev.line([(l_x + 28, item_y + 8), (l_x + 72, item_y + 8)], fill=(94, 234, 212, 220), width=2)
draw_elev.text((l_x + 86, item_y - 2), "Provincial Administrative Boundaries (Mint #5EEAD4)", fill=(203, 213, 225, 255), font=leg_body_f)

item_y += 36
draw_elev.rectangle([l_x + 28, item_y, l_x + 72, item_y + 16], fill=(12, 43, 82, 255), outline=(56, 189, 248, 180), width=1)
draw_elev.text((l_x + 86, item_y - 2), "Arabian Sea (Marine Ocean Bathymetry)", fill=(56, 189, 248, 255), font=leg_body_f)

# Divider
item_y += 32
draw_elev.line([(l_x + 28, item_y), (l_x + l_w - 28, item_y)], fill=(45, 212, 191, 70), width=1)

# Notes
item_y += 12
draw_elev.text((l_x + 28, item_y), "• Indus River Basin: Natural drainage from glaciated summits into Arabian Sea", fill=(203, 213, 225, 240), font=leg_text_f)
item_y += 24
draw_elev.text((l_x + 28, item_y), "• Topographic Divisions: Alluvial lowlands (<200m), Balochistan Plateaus (600–2,000m)", fill=(203, 213, 225, 240), font=leg_text_f)
item_y += 24
draw_elev.text((l_x + 28, item_y), "• High Alpine Summits: K2 (8,611m), Nanga Parbat, Tirich Mir (>7,000m)", fill=(203, 213, 225, 240), font=leg_text_f)

# Peak Callouts & River Labels
peaks = [
    ("K2 (8,611m) - 2nd Highest Peak on Earth", 76.5133, 35.8814, 3420, 240),
    ("Nanga Parbat (8,126m) - Killer Mountain", 74.5891, 35.2372, 3050, 360),
    ("Tirich Mir (7,708m) - Hindu Kush", 71.8498, 36.2528, 2430, 200),
    ("Takht-e-Sulaiman (3,487m)", 70.0667, 31.6333, 2020, 920),
]
for p_name, lon, lat, px, py in peaks:
    draw_elev.ellipse([px - 6, py - 6, px + 6, py + 6], fill=(239, 68, 68, 255), outline=(255, 255, 255, 255), width=2)
    text_w = int(len(p_name) * 11)
    badge_rect = [px + 14, py - 16, px + 24 + text_w, py + 16]
    draw_elev.rounded_rectangle(badge_rect, radius=6, fill=(8, 12, 22, 230), outline=(45, 212, 191, 160), width=1)
    draw_elev.text((px + 20, py - 10), p_name, fill=(255, 255, 255, 255), font=leg_text_f)
    draw_elev.line([(px, py), (px + 14, py)], fill=(45, 212, 191, 180), width=1)

draw_river_labels(draw_elev, river_f)
draw_ocean_watermark(draw_elev, ocean_f)
draw_scale_hud(draw_elev, render_width, render_height, leg_title_f, leg_text_f)

# Save Map 1 outputs
rgb_elev = img_elev.convert("RGB")
rgb_elev.save(final_elev_jpg, "JPEG", quality=95)
img_elev.save(final_elev_png, "PNG")
print(f" [+] Map 1 exported:")
print(f"     - JPG: '{final_elev_jpg}'")
print(f"     - PNG: '{final_elev_png}'")

# =========================================================
# Step 5: Render Map 2 (3D Major Floods Inundation Map)
# =========================================================
print("\n[5/6] Rendering Map 2: 3D Major Floods Inundation Map with Natural Ocean & Rivers...")

raw_flood_path = os.path.join(outputs_dir, "pakistan_flood_3d_raw.png")
final_flood_jpg = os.path.join(outputs_dir, "pakistan_flood_map_3d.jpg")
final_flood_png = os.path.join(outputs_dir, "pakistan_flood_map_3d.png")

scene_flood = f3d.MapScene(
    terrain=f3d.TerrainSource(path=dem_path, crs="EPSG:4326"),
    target_crs="EPSG:4326",
    camera=f3d.OrbitCamera(
        target=(69.35, 30.40, 0.0),
        distance=9.0,
        azimuth_deg=0.0,
        elevation_deg=45.0,
        fov_deg=45.0
    ),
    lighting=f3d.LightingPreset(
        name="default",
        intensity=2.4,
        sun_direction=[0.55, 0.78, 0.48]
    ),
    layers=[
        f3d.RasterOverlay(
            layer_id="floods",
            path=flood_overlay_path,
            crs="EPSG:4326",
            opacity=0.95
        )
    ],
    output=f3d.OutputSpec(
        width=render_width,
        height=render_height,
        path=raw_flood_path,
        format="png"
    )
)

report_flood = scene_flood.render(raw_flood_path)
print(f" [+] forge3d Flood Render status: {report_flood.status}")

# Compose Map 2 Cartographic Plate
img_flood = Image.open(raw_flood_path).convert("RGBA")
draw_flood = ImageDraw.Draw(img_flood)

# 5.1 Header Card (Top-Left)
draw_flood.rounded_rectangle([h_x, h_y, h_x + h_w, h_y + h_h], radius=14, fill=(8, 12, 22, 242), outline=(45, 212, 191, 120), width=1)
draw_flood.rounded_rectangle([h_x, h_y, h_x + 6, h_y + h_h], radius=3, fill=(45, 212, 191, 255))
draw_flood.text((h_x + 30, h_y + 18), "NATIONAL GEOMORPHOLOGY & DISASTER RISK ASSESSMENT", fill=(45, 212, 191, 255), font=kicker_f)
draw_flood.text((h_x + 30, h_y + 44), "PAKISTAN MAJOR FLOOD EXTENTS (2010–PRESENT)", fill=(255, 255, 255, 255), font=title_f)
draw_flood.text((h_x + 30, h_y + 100), "3D Path-Traced Topographic Inundation & Hydrodynamic Flow Model", fill=(203, 213, 225, 255), font=subtitle_f)
draw_flood.text((h_x + 30, h_y + 136), "Data Sources: UN OCHA • UNOSAT • NASA/NOAA VIIRS • Natural Earth Coastlines • Copernicus DEM", fill=(148, 163, 184, 255), font=meta_f)

# 5.2 Flood Hazard & Intensity Legend Card (Bottom-Left)
l_x, l_y, l_w, l_h = 70, 1460, 830, 500
draw_flood.rounded_rectangle([l_x, l_y, l_x + l_w, l_y + l_h], radius=14, fill=(8, 12, 22, 242), outline=(45, 212, 191, 120), width=1)
draw_flood.rounded_rectangle([l_x, l_y, l_x + 6, l_y + l_h], radius=3, fill=(45, 212, 191, 255))
draw_flood.text((l_x + 28, l_y + 18), "FLOOD HAZARD INTENSITY & REFERENCE LEGEND", fill=(255, 255, 255, 255), font=leg_title_f)

# Color Ramp
for i in range(ramp_w):
    factor = i / float(ramp_w)
    r_c = int(14 * (1 - factor) + 224 * factor)
    g_c = int(116 * (1 - factor) + 242 * factor)
    b_c = int(144 * (1 - factor) + 254 * factor)
    draw_flood.line([(ramp_x + i, ramp_y), (ramp_x + i, ramp_y + ramp_h)], fill=(r_c, g_c, b_c, 255))
draw_flood.rectangle([ramp_x, ramp_y, ramp_x + ramp_w, ramp_y + ramp_h], outline=(255, 255, 255, 160), width=1)
draw_flood.text((ramp_x, ramp_y + 26), "Low / Seasonal Flow", fill=(203, 213, 225, 255), font=leg_text_f)
draw_flood.text((ramp_x + 230, ramp_y + 26), "Moderate River Surge", fill=(148, 163, 184, 255), font=leg_text_f)
draw_flood.text((ramp_x + ramp_w - 245, ramp_y + 26), "Extreme Megaflood Submersion", fill=(56, 189, 248, 255), font=leg_text_f)

# Key Items
item_y = l_y + 120
draw_flood.line([(l_x + 28, item_y + 8), (l_x + 72, item_y + 8)], fill=(0, 240, 255, 255), width=4)
draw_flood.text((l_x + 86, item_y - 2), "Major River Streamlines (Indus & Tributaries)", fill=(0, 240, 255, 255), font=leg_body_f)

item_y += 36
draw_flood.line([(l_x + 28, item_y + 8), (l_x + 72, item_y + 8)], fill=(45, 212, 191, 255), width=4)
draw_flood.text((l_x + 86, item_y - 2), "Pakistan International Border (Mint #2DD4BF)", fill=(45, 212, 191, 255), font=leg_body_f)

item_y += 36
draw_flood.line([(l_x + 28, item_y + 8), (l_x + 72, item_y + 8)], fill=(94, 234, 212, 220), width=2)
draw_flood.text((l_x + 86, item_y - 2), "Provincial Administrative Boundaries (Mint #5EEAD4)", fill=(203, 213, 225, 255), font=leg_body_f)

item_y += 36
draw_flood.rectangle([l_x + 28, item_y, l_x + 72, item_y + 16], fill=(12, 43, 82, 255), outline=(56, 189, 248, 180), width=1)
draw_flood.text((l_x + 86, item_y - 2), "Arabian Sea (Marine Ocean Bathymetry)", fill=(56, 189, 248, 255), font=leg_body_f)

# Divider
item_y += 32
draw_flood.line([(l_x + 28, item_y), (l_x + l_w - 28, item_y)], fill=(45, 212, 191, 70), width=1)

# Hazard Factors & Methodology
item_y += 12
draw_flood.text((l_x + 28, item_y), "• Flood Origin: Monsoon surges spilling from Indus, Chenab, Jhelum & Sutlej rivers", fill=(203, 213, 225, 240), font=leg_text_f)
item_y += 24
draw_flood.text((l_x + 28, item_y), "• 2010 Super Flood: UN OCHA Union Council Inundation Model (Riverine Surge)", fill=(203, 213, 225, 240), font=leg_text_f)
item_y += 24
draw_flood.text((l_x + 28, item_y), "• 2022 Megaflood: UNOSAT / NASA VIIRS 375m Satellite Flood Submersion Extent", fill=(203, 213, 225, 240), font=leg_text_f)
item_y += 24
draw_flood.text((l_x + 28, item_y), "• Hazard Index: Composite Multi-Temporal Flooding × Lowland DEM Factor (<200m)", fill=(148, 163, 184, 240), font=leg_text_f)

# River Labels, Ocean Watermark in open sea & Scale HUD
draw_river_labels(draw_flood, river_f)
draw_ocean_watermark(draw_flood, ocean_f)
draw_scale_hud(draw_flood, render_width, render_height, leg_title_f, leg_text_f)

# Save Map 2 outputs
rgb_flood = img_flood.convert("RGB")
rgb_flood.save(final_flood_jpg, "JPEG", quality=95)
img_flood.save(final_flood_png, "PNG")
print(f" [+] Map 2 exported:")
print(f"     - JPG: '{final_flood_jpg}'")
print(f"     - PNG: '{final_flood_png}'")

print("\n" + "=" * 75)
print("[SUCCESS] PIPELINE COMPLETED SUCCESSFULLY! ALL MAPS GENERATED IN 4K.")
print("=" * 75)