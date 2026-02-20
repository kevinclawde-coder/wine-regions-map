#!/usr/bin/env python3
"""Phase 1: Scan z5 globally for both tilesets, extract all region names + polygons.
Uses mapbox_vector_tile to decode, then converts pixel coords to lat/lng properly."""

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
EXTENT = 4096  # Default MVT extent

# Wine-producing land bounds (lat_min, lng_min, lat_max, lng_max)
WINE_BOUNDS = [
    (35, -10, 55, 45),      # Europe
    (-35, 17, -27, 33),     # South Africa
    (-43, 112, -20, 155),   # Australia
    (-47, 166, -34, 179),   # New Zealand
    (-43, -73, -22, -58),   # South America
    (25, -128, 50, -65),    # North America
    (28, -5, 42, 55),       # N. Africa / Middle East
    (28, 75, 48, 135),      # East Asia
    (18, -117, 32, -95),    # Mexico
    (-5, 34, 5, 40),        # East Africa (Kenya, Ethiopia)
    (30, 34, 34, 36),       # Israel/Lebanon
]

def lat_lng_to_tile(lat, lng, zoom):
    """Convert lat/lng to tile x,y at given zoom."""
    n = 2 ** zoom
    x = int((lng + 180) / 360 * n)
    lat_rad = math.radians(lat)
    y = int((1 - math.log(math.tan(lat_rad) + 1/math.cos(lat_rad)) / math.pi) / 2 * n)
    return max(0, min(n-1, x)), max(0, min(n-1, y))

def tile_to_lng(x, zoom):
    """Get longitude of tile's left edge."""
    n = 2 ** zoom
    return x / n * 360 - 180

def tile_to_lat(y, zoom):
    """Get latitude of tile's top edge."""
    n = 2 ** zoom
    lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * y / n)))
    return math.degrees(lat_rad)

def pixel_to_latlng(px, py, tx, ty, zoom, extent=EXTENT):
    """Convert pixel coordinates within a tile to lat/lng.
    px, py: pixel coords (0 to extent)
    tx, ty: tile x, y
    zoom: zoom level
    extent: tile extent (default 4096)
    """
    # Convert pixel to fractional tile position
    frac_x = tx + px / extent
    frac_y = ty + py / extent
    
    # Convert to lng
    n = 2 ** zoom
    lng = frac_x / n * 360 - 180
    
    # Convert to lat
    lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * frac_y / n)))
    lat = math.degrees(lat_rad)
    
    return lng, lat

def convert_geometry(geom, tx, ty, zoom, extent=EXTENT):
    """Convert a geometry's coordinates from tile pixels to lat/lng."""
    geom_type = geom['type']
    
    def convert_ring(ring):
        return [pixel_to_latlng(p[0], p[1], tx, ty, zoom, extent) for p in ring]
    
    if geom_type == 'Polygon':
        new_coords = [convert_ring(ring) for ring in geom['coordinates']]
        return {'type': 'Polygon', 'coordinates': new_coords}
    elif geom_type == 'MultiPolygon':
        new_coords = [[convert_ring(ring) for ring in poly] for poly in geom['coordinates']]
        return {'type': 'MultiPolygon', 'coordinates': new_coords}
    elif geom_type == 'Point':
        lng, lat = pixel_to_latlng(geom['coordinates'][0], geom['coordinates'][1], tx, ty, zoom, extent)
        return {'type': 'Point', 'coordinates': [lng, lat]}
    elif geom_type == 'LineString':
        new_coords = [pixel_to_latlng(p[0], p[1], tx, ty, zoom, extent) for p in geom['coordinates']]
        return {'type': 'LineString', 'coordinates': new_coords}
    else:
        print(f"  Warning: unsupported geometry type {geom_type}")
        return geom

def get_wine_tiles(zoom):
    """Get all tile x,y coords at given zoom that overlap wine regions."""
    tiles = set()
    for lat_min, lng_min, lat_max, lng_max in WINE_BOUNDS:
        x_min, y_max = lat_lng_to_tile(lat_min, lng_min, zoom)
        x_max, y_min = lat_lng_to_tile(lat_max, lng_max, zoom)
        for x in range(x_min, x_max + 1):
            for y in range(y_min, y_max + 1):
                tiles.add((x, y))
    return sorted(tiles)

def fetch_tile(tileset_id, layer_name, z, x, y):
    """Fetch and decode a single tile, returning list of (properties, geometry_latlng)."""
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
            
            # Only process polygons/multipolygons
            if geom['type'] not in ('Polygon', 'MultiPolygon'):
                continue
            
            converted_geom = convert_geometry(geom, x, y, z, extent)
            results.append((props, converted_geom))
        
        return results
    except Exception as e:
        return []

def extract_tileset(tileset_id, layer_name, zoom, label):
    """Extract all features from a tileset at given zoom level."""
    tiles = get_wine_tiles(zoom)
    print(f"\n{'='*60}")
    print(f"Extracting {label}")
    print(f"Tileset: {tileset_id}, Layer: {layer_name}, Zoom: {zoom}")
    print(f"Tiles to scan: {len(tiles)}")
    print(f"{'='*60}")
    
    # Collect all fragments keyed by region name + country
    region_fragments = defaultdict(lambda: {'properties': None, 'geometries': []})
    total_features = 0
    tiles_with_data = 0
    
    def process_tile(tile):
        x, y = tile
        return (x, y, fetch_tile(tileset_id, layer_name, zoom, x, y))
    
    with ThreadPoolExecutor(max_workers=15) as executor:
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
            
            if done % 20 == 0 or done == len(tiles):
                print(f"  Progress: {done}/{len(tiles)} tiles, {tiles_with_data} with data, {total_features} feature fragments, {len(region_fragments)} unique regions")
    
    print(f"\nMerging fragments for {len(region_fragments)} unique regions...")
    
    # Merge fragments per region using Shapely
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
                        # Try to fix with buffer(0)
                        fixed = s.buffer(0)
                        if not fixed.is_empty:
                            shapely_geoms.append(fixed)
                except Exception:
                    pass
            
            if not shapely_geoms:
                errors += 1
                continue
            
            merged = unary_union(shapely_geoms)
            
            # Ensure it's a MultiPolygon or Polygon
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
            
            feature = {
                'type': 'Feature',
                'properties': data['properties'],
                'geometry': mapping(merged)
            }
            features.append(feature)
        except Exception as e:
            errors += 1
    
    print(f"Merged: {len(features)} regions ({errors} errors)")
    
    # Count by country
    countries = defaultdict(int)
    for f in features:
        c = f['properties'].get('country', 'unknown')
        countries[c] += 1
    
    print(f"\nRegions by country:")
    for c, n in sorted(countries.items(), key=lambda x: -x[1])[:20]:
        print(f"  {c}: {n}")
    
    return features

def validate_coordinates(features, label):
    """Validate that coordinates are in valid ranges."""
    bad = 0
    for f in features:
        geom = f['geometry']
        coords_flat = []
        if geom['type'] == 'Polygon':
            for ring in geom['coordinates']:
                coords_flat.extend(ring)
        elif geom['type'] == 'MultiPolygon':
            for poly in geom['coordinates']:
                for ring in poly:
                    coords_flat.extend(ring)
        
        for lng, lat in coords_flat:
            if lat < -90 or lat > 90 or lng < -180 or lng > 180:
                bad += 1
                break
    
    print(f"{label}: {len(features)} features, {bad} with out-of-range coords")
    return bad == 0

if __name__ == '__main__':
    start = time.time()
    
    # Extract middle tileset (the big gap)
    middle_features = extract_tileset(
        'martinvonwyss.44iyix54',
        'mstr_middle-3ysqay',
        5,
        'MIDDLE TILESET (z5)'
    )
    
    # Save intermediate
    middle_geojson = {'type': 'FeatureCollection', 'features': middle_features}
    with open('middle_regions_z5.json', 'w') as f:
        json.dump(middle_geojson, f)
    print(f"Saved middle_regions_z5.json ({len(middle_features)} features)")
    
    # Extract top tileset
    top_features = extract_tileset(
        'martinvonwyss.5n5kv3km',
        'master_top_20_04-bxq4qz',
        5,
        'TOP TILESET (z5)'
    )
    
    # Save intermediate
    top_geojson = {'type': 'FeatureCollection', 'features': top_features}
    with open('top_regions_z5.json', 'w') as f:
        json.dump(top_geojson, f)
    print(f"Saved top_regions_z5.json ({len(top_features)} features)")
    
    # Validate
    print("\n=== VALIDATION ===")
    validate_coordinates(middle_features, "Middle")
    validate_coordinates(top_features, "Top")
    
    elapsed = time.time() - start
    print(f"\nPhase 1 complete in {elapsed:.1f}s")
    print(f"Middle: {len(middle_features)} regions")
    print(f"Top: {len(top_features)} regions")
    print(f"Total new: {len(middle_features) + len(top_features)} regions")
