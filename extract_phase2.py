#!/usr/bin/env python3
"""Phase 2: Scan z6 for both tilesets in key wine areas for better detail.
Then z7 for Europe's densest areas (France, Italy)."""

import json
import math
import time
import requests
import mapbox_vector_tile
from concurrent.futures import ThreadPoolExecutor, as_completed
from shapely.geometry import shape, mapping, MultiPolygon, Polygon, GeometryCollection
from shapely.ops import unary_union
from collections import defaultdict

TOKEN = 'pk.eyJ1IjoibWFydGludm9ud3lzcyIsImEiOiJ5UDZqNzlRIn0.C2v_MaFow7-hiueBnq2ELQ'
EXTENT = 4096

def lat_lng_to_tile(lat, lng, zoom):
    n = 2 ** zoom
    x = int((lng + 180) / 360 * n)
    lat_rad = math.radians(lat)
    y = int((1 - math.log(math.tan(lat_rad) + 1/math.cos(lat_rad)) / math.pi) / 2 * n)
    return max(0, min(n-1, x)), max(0, min(n-1, y))

def pixel_to_latlng(px, py, tx, ty, zoom, extent=EXTENT):
    frac_x = tx + px / extent
    frac_y = ty + py / extent
    n = 2 ** zoom
    lng = frac_x / n * 360 - 180
    lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * frac_y / n)))
    lat = math.degrees(lat_rad)
    return lng, lat

def convert_geometry(geom, tx, ty, zoom, extent=EXTENT):
    geom_type = geom['type']
    def convert_ring(ring):
        return [pixel_to_latlng(p[0], p[1], tx, ty, zoom, extent) for p in ring]
    if geom_type == 'Polygon':
        return {'type': 'Polygon', 'coordinates': [convert_ring(r) for r in geom['coordinates']]}
    elif geom_type == 'MultiPolygon':
        return {'type': 'MultiPolygon', 'coordinates': [[convert_ring(r) for r in poly] for poly in geom['coordinates']]}
    return geom

def get_tiles_for_bounds(bounds_list, zoom):
    tiles = set()
    for lat_min, lng_min, lat_max, lng_max in bounds_list:
        x_min, y_max = lat_lng_to_tile(lat_min, lng_min, zoom)
        x_max, y_min = lat_lng_to_tile(lat_max, lng_max, zoom)
        for x in range(x_min, x_max + 1):
            for y in range(y_min, y_max + 1):
                tiles.add((x, y))
    return sorted(tiles)

def fetch_tile(tileset_id, layer_name, z, x, y):
    url = f'https://a.tiles.mapbox.com/v4/{tileset_id}/{z}/{x}/{y}.vector.pbf?access_token={TOKEN}'
    try:
        r = requests.get(url, timeout=15)
        if r.status_code == 404 or len(r.content) == 0:
            return []
        if r.status_code == 429:
            time.sleep(2)
            r = requests.get(url, timeout=15)
        if r.status_code != 200:
            return []
        decoded = mapbox_vector_tile.decode(r.content)
        if layer_name not in decoded:
            return []
        features = decoded[layer_name]['features']
        extent = decoded[layer_name].get('extent', EXTENT)
        results = []
        for feat in features:
            geom = feat['geometry']
            props = feat['properties']
            if geom['type'] not in ('Polygon', 'MultiPolygon'):
                continue
            converted_geom = convert_geometry(geom, x, y, z, extent)
            results.append((props, converted_geom))
        return results
    except Exception as e:
        return []

def extract_tileset(tileset_id, layer_name, zoom, bounds, label):
    tiles = get_tiles_for_bounds(bounds, zoom)
    print(f"\n{'='*60}")
    print(f"Extracting {label}")
    print(f"Tileset: {tileset_id}, Zoom: {zoom}, Tiles: {len(tiles)}")
    print(f"{'='*60}")
    
    region_fragments = defaultdict(lambda: {'properties': None, 'geometries': []})
    total_features = 0
    tiles_with_data = 0
    
    def process_tile(tile):
        x, y = tile
        return (x, y, fetch_tile(tileset_id, layer_name, zoom, x, y))
    
    with ThreadPoolExecutor(max_workers=18) as executor:
        futures = {executor.submit(process_tile, t): t for t in tiles}
        done = 0
        for future in as_completed(futures):
            done += 1
            x, y, results = future.result()
            if results:
                tiles_with_data += 1
                for props, geom in results:
                    name = props.get('Name_rgn', 'unknown')
                    country = props.get('country', 'unknown')
                    key = f"{name}|{country}"
                    if region_fragments[key]['properties'] is None:
                        region_fragments[key]['properties'] = props
                    region_fragments[key]['geometries'].append(geom)
                    total_features += 1
            if done % 50 == 0 or done == len(tiles):
                print(f"  {done}/{len(tiles)} tiles, {tiles_with_data} with data, {total_features} fragments, {len(region_fragments)} unique")
    
    print(f"\nMerging fragments for {len(region_fragments)} regions...")
    features = []
    errors = 0
    for key, data in region_fragments.items():
        try:
            shapely_geoms = []
            for g in data['geometries']:
                try:
                    s = shape(g)
                    if s.is_valid and not s.is_empty:
                        shapely_geoms.append(s)
                    else:
                        fixed = s.buffer(0)
                        if not fixed.is_empty:
                            shapely_geoms.append(fixed)
                except:
                    pass
            if not shapely_geoms:
                errors += 1
                continue
            merged = unary_union(shapely_geoms)
            if isinstance(merged, GeometryCollection):
                polys = [g for g in merged.geoms if isinstance(g, (Polygon, MultiPolygon))]
                if polys:
                    merged = unary_union(polys)
                else:
                    errors += 1
                    continue
            if isinstance(merged, Polygon):
                merged = MultiPolygon([merged])
            if merged.is_empty:
                errors += 1
                continue
            features.append({
                'type': 'Feature',
                'properties': data['properties'],
                'geometry': mapping(merged)
            })
        except Exception as e:
            errors += 1
    
    print(f"  Result: {len(features)} regions ({errors} errors)")
    countries = defaultdict(int)
    for f in features:
        countries[f['properties'].get('country', '?')] += 1
    for c, n in sorted(countries.items(), key=lambda x: -x[1])[:10]:
        print(f"    {c}: {n}")
    
    return features

if __name__ == '__main__':
    start = time.time()
    
    # Key wine regions for z6 detail
    z6_bounds = [
        (35, -10, 55, 45),      # Europe
        (-35, 17, -27, 33),     # South Africa
        (-43, 112, -20, 155),   # Australia
        (-47, 166, -34, 179),   # New Zealand
        (-43, -73, -22, -58),   # South America
        (25, -128, 50, -65),    # North America
        (28, -5, 42, 55),       # N. Africa / Middle East
        (28, 75, 48, 135),      # East Asia
        (18, -117, 32, -95),    # Mexico
    ]
    
    # Dense Europe areas for z7
    z7_bounds = [
        (41, -5, 52, 20),   # France, N. Italy, Switzerland, Germany
        (36, 6, 47, 19),    # Italy
        (36, -10, 44, 0),   # Spain, Portugal
    ]
    
    # Middle tileset z6
    middle_z6 = extract_tileset(
        'martinvonwyss.44iyix54', 'mstr_middle-3ysqay',
        6, z6_bounds, 'MIDDLE z6 (global)'
    )
    with open('middle_regions_z6.json', 'w') as f:
        json.dump({'type': 'FeatureCollection', 'features': middle_z6}, f)
    print(f"Saved middle_regions_z6.json ({len(middle_z6)} features)")
    
    # Top tileset z6
    top_z6 = extract_tileset(
        'martinvonwyss.5n5kv3km', 'master_top_20_04-bxq4qz',
        6, z6_bounds, 'TOP z6 (global)'
    )
    with open('top_regions_z6.json', 'w') as f:
        json.dump({'type': 'FeatureCollection', 'features': top_z6}, f)
    print(f"Saved top_regions_z6.json ({len(top_z6)} features)")
    
    # Middle tileset z7 for Europe detail
    middle_z7 = extract_tileset(
        'martinvonwyss.44iyix54', 'mstr_middle-3ysqay',
        7, z7_bounds, 'MIDDLE z7 (Europe detail)'
    )
    with open('middle_regions_z7.json', 'w') as f:
        json.dump({'type': 'FeatureCollection', 'features': middle_z7}, f)
    print(f"Saved middle_regions_z7.json ({len(middle_z7)} features)")
    
    # Top tileset z7 for Europe
    top_z7 = extract_tileset(
        'martinvonwyss.5n5kv3km', 'master_top_20_04-bxq4qz',
        7, z7_bounds, 'TOP z7 (Europe detail)'
    )
    with open('top_regions_z7.json', 'w') as f:
        json.dump({'type': 'FeatureCollection', 'features': top_z7}, f)
    print(f"Saved top_regions_z7.json ({len(top_z7)} features)")
    
    elapsed = time.time() - start
    print(f"\n{'='*60}")
    print(f"Phase 2 complete in {elapsed:.1f}s")
    print(f"Middle z6: {len(middle_z6)}, Top z6: {len(top_z6)}")
    print(f"Middle z7: {len(middle_z7)}, Top z7: {len(top_z7)}")
    print(f"We'll use the highest zoom available per region in Phase 3")
