import requests
import json
from shapely.geometry import LineString
import geopandas as gpd
import os

overpass_url = "https://overpass-api.de/api/interpreter"
query = """[out:json][timeout:35];
(
  relation["waterway"="river"]["name"~"Indus|Jhelum|Chenab|Ravi|Sutlej|Kabul|Swat|Kurram|Gomal|Hingol|Zhob|Hub|Panjnad|Gilgit|Hunza|Shyok|Kunhar|Soan|Nari|Porali",i](23.5,60.7,37.3,78.0);
  way["waterway"="river"]["name"~"Indus|Jhelum|Chenab|Ravi|Sutlej|Kabul|Swat|Kurram|Gomal|Hingol|Zhob|Hub|Panjnad|Gilgit|Hunza|Shyok|Kunhar|Soan|Nari|Porali",i](23.5,60.7,37.3,78.0);
);
out body;
>;
out skel qt;"""

os.makedirs("data/rivers", exist_ok=True)
try:
    print("Querying OSM Overpass for major Pakistan river tributaries...")
    r = requests.post(overpass_url, data={"data": query}, timeout=40)
    data = r.json()
    nodes = {node["id"]: (node["lon"], node["lat"]) for node in data["elements"] if node["type"] == "node"}
    
    lines = []
    names = []
    for elem in data["elements"]:
        if elem["type"] == "way" and "nodes" in elem:
            coords = [nodes[n] for n in elem["nodes"] if n in nodes]
            if len(coords) >= 2:
                lines.append(LineString(coords))
                names.append(elem.get("tags", {}).get("name:en", elem.get("tags", {}).get("name", "River")))
                
    if lines:
        gdf = gpd.GeoDataFrame({"name": names, "geometry": lines}, crs="EPSG:4326")
        out_path = "data/rivers/pakistan_major_rivers.geojson"
        gdf.to_file(out_path, driver="GeoJSON")
        print(f"Saved {len(gdf)} river segments to {out_path}!")
        print("Rivers captured:", len(gdf))
    else:
        print("No OSM river lines returned, fallback to Natural Earth.")
except Exception as e:
    print("Fetch error:", e)
