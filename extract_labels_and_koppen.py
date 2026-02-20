#!/usr/bin/env python3
"""
Extract label metadata and Köppen climate data from Martin's Mapbox tilesets.
Tilesets:
  1. martinvonwyss.3oqku56u — layer: m_top_labels-9isyyt (z0-10) — Top-level region labels
  2. martinvonwyss.8bnug1jj — layer: m_sbrgn_labels-afx25k (z0-10) — Sub-region labels
  3. martinvonwyss.b8gzaigx — layer: Lbls_middle-764v5m (z0-10) — Middle-level labels
  4. martinvonwyss.51oes0lc — layer: Koeppen_5m_WM-9q9klu (z3-8) — Köppen climate zones
"""

import json
import math
import gzip
import requests
import mapbox_vector_tile
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict
import time
import sys
import os

TOKEN = "pk.eyJ1IjoibWFydGludm9ud3lzcyIsImEiOiJ5UDZqNzlRIn0.C2v_MaFow7-hiueBnq2ELQ"

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
]

# --- Coordinate math ---

def lat_lng_to_tile(lat, lng, zoom):
    """Convert lat/lng to tile x/y at given zoom."""
    n = 2 ** zoom
    x = int((lng + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int((1.0 - math.log(math.tan(lat_rad) + 1.0/math.cos(lat_rad)) / math.pi) / 2.0 * n)
    x = max(0, min(n-1, x))
    y = max(0, min(n-1, y))
    return x, y

def tile_to_lat_lng(x, y, z):
    """Convert tile coordinates to lat/lng (top-left corner)."""
    n = 2 ** z
    lng = x / n * 360.0 - 180.0
    lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * y / n)))
    lat = math.degrees(lat_rad)
    return lat, lng

def pixel_to_lat_lng(tile_x, tile_y, zoom, pixel_x, pixel_y, extent=4096):
    """Convert pixel coords within a tile to lat/lng."""
    n = 2 ** zoom
    lng = (tile_x + pixel_x / extent) / n * 360.0 - 180.0
    lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * (tile_y + pixel_y / extent) / n)))
    lat = math.degrees(lat_rad)
    return lat, lng

def get_tiles_for_bounds(bounds_list, zoom):
    """Get all tile x,y pairs covering the given bounds at zoom level."""
    tiles = set()
    for lat_min, lng_min, lat_max, lng_max in bounds_list:
        x_min, y_max = lat_lng_to_tile(lat_min, lng_min, zoom)
        x_max, y_min = lat_lng_to_tile(lat_max, lng_max, zoom)
        # Handle wrap-around
        if x_min > x_max:
            # Crosses antimeridian
            n = 2 ** zoom
            for x in range(x_min, n):
                for y in range(y_min, y_max + 1):
                    tiles.add((x, y))
            for x in range(0, x_max + 1):
                for y in range(y_min, y_max + 1):
                    tiles.add((x, y))
        else:
            for x in range(x_min, x_max + 1):
                for y in range(y_min, y_max + 1):
                    tiles.add((x, y))
    return list(tiles)

# --- Tile fetching ---

def fetch_tile(tileset_id, z, x, y):
    """Fetch and decode a single MVT tile."""
    url = f"https://api.mapbox.com/v4/{tileset_id}/{z}/{x}/{y}.vector.pbf?access_token={TOKEN}"
    try:
        resp = requests.get(url, timeout=30)
        if resp.status_code == 404:
            return None
        if resp.status_code == 204:
            return None
        resp.raise_for_status()
        data = resp.content
        # Try gzip decompress
        try:
            data = gzip.decompress(data)
        except:
            pass
        decoded = mapbox_vector_tile.decode(data)
        return decoded
    except Exception as e:
        return None

def extract_features_from_tile(decoded, layer_name, tile_x, tile_y, zoom):
    """Extract features from a decoded tile, converting geometry to lat/lng."""
    if not decoded or layer_name not in decoded:
        return []
    
    layer = decoded[layer_name]
    extent = layer.get('extent', 4096)
    features = []
    
    for feat in layer.get('features', []):
        geom = feat.get('geometry', {})
        geom_type = geom.get('type', '')
        props = dict(feat.get('properties', {}))
        
        if geom_type == 'Point':
            coords = geom.get('coordinates', [])
            if coords:
                lat, lng = pixel_to_lat_lng(tile_x, tile_y, zoom, coords[0], coords[1], extent)
                features.append({
                    'type': 'Feature',
                    'geometry': {
                        'type': 'Point',
                        'coordinates': [round(lng, 6), round(lat, 6)]
                    },
                    'properties': props
                })
        elif geom_type in ('Polygon', 'MultiPolygon'):
            # Convert polygon coordinates
            converted_geom = convert_polygon_geometry(geom, tile_x, tile_y, zoom, extent)
            features.append({
                'type': 'Feature',
                'geometry': converted_geom,
                'properties': props
            })
        elif geom_type == 'MultiPoint':
            coords_list = geom.get('coordinates', [])
            for coords in coords_list:
                lat, lng = pixel_to_lat_lng(tile_x, tile_y, zoom, coords[0], coords[1], extent)
                features.append({
                    'type': 'Feature',
                    'geometry': {
                        'type': 'Point',
                        'coordinates': [round(lng, 6), round(lat, 6)]
                    },
                    'properties': props
                })
        elif geom_type == 'LineString':
            coords_list = geom.get('coordinates', [])
            converted = []
            for c in coords_list:
                lat, lng = pixel_to_lat_lng(tile_x, tile_y, zoom, c[0], c[1], extent)
                converted.append([round(lng, 6), round(lat, 6)])
            features.append({
                'type': 'Feature',
                'geometry': {'type': 'LineString', 'coordinates': converted},
                'properties': props
            })
        elif geom_type == 'MultiLineString':
            all_lines = []
            for line in geom.get('coordinates', []):
                converted = []
                for c in line:
                    lat, lng = pixel_to_lat_lng(tile_x, tile_y, zoom, c[0], c[1], extent)
                    converted.append([round(lng, 6), round(lat, 6)])
                all_lines.append(converted)
            features.append({
                'type': 'Feature',
                'geometry': {'type': 'MultiLineString', 'coordinates': all_lines},
                'properties': props
            })
    
    return features

def convert_polygon_geometry(geom, tile_x, tile_y, zoom, extent):
    """Convert polygon/multipolygon coordinates from tile space to lat/lng."""
    geom_type = geom.get('type', '')
    
    if geom_type == 'Polygon':
        rings = []
        for ring in geom.get('coordinates', []):
            converted = []
            for c in ring:
                lat, lng = pixel_to_lat_lng(tile_x, tile_y, zoom, c[0], c[1], extent)
                converted.append([round(lng, 6), round(lat, 6)])
            rings.append(converted)
        return {'type': 'Polygon', 'coordinates': rings}
    elif geom_type == 'MultiPolygon':
        polys = []
        for poly in geom.get('coordinates', []):
            rings = []
            for ring in poly:
                converted = []
                for c in ring:
                    lat, lng = pixel_to_lat_lng(tile_x, tile_y, zoom, c[0], c[1], extent)
                    converted.append([round(lng, 6), round(lat, 6)])
                rings.append(converted)
            polys.append(rings)
        return {'type': 'MultiPolygon', 'coordinates': polys}
    return geom

# --- Main extraction ---

def extract_tileset(tileset_id, layer_name, zoom, description, max_workers=18):
    """Extract all features from a tileset at given zoom level."""
    tiles = get_tiles_for_bounds(WINE_BOUNDS, zoom)
    print(f"\n{'='*60}")
    print(f"Extracting: {description}")
    print(f"  Tileset: {tileset_id}, Layer: {layer_name}, Zoom: {zoom}")
    print(f"  Tiles to fetch: {len(tiles)}")
    
    all_features = []
    fetched = 0
    errors = 0
    empty = 0
    
    def process_tile(tile):
        x, y = tile
        decoded = fetch_tile(tileset_id, zoom, x, y)
        if decoded is None:
            return [], True  # empty
        features = extract_features_from_tile(decoded, layer_name, x, y, zoom)
        return features, False
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(process_tile, t): t for t in tiles}
        for future in as_completed(futures):
            fetched += 1
            try:
                features, was_empty = future.result()
                if was_empty:
                    empty += 1
                all_features.extend(features)
            except Exception as e:
                errors += 1
            
            if fetched % 50 == 0 or fetched == len(tiles):
                print(f"  Progress: {fetched}/{len(tiles)} tiles, {len(all_features)} features, {empty} empty, {errors} errors")
    
    print(f"  Done! Total features: {len(all_features)}")
    return all_features

def dedup_label_features(features):
    """Deduplicate label features by Name_rgn + country."""
    seen = {}
    deduped = []
    for f in features:
        props = f.get('properties', {})
        name = props.get('Name_rgn', props.get('name', ''))
        country = props.get('country', '')
        key = (name, country)
        if key and key not in seen:
            seen[key] = True
            deduped.append(f)
        elif not name:
            # Keep features without Name_rgn (they might use other identifiers)
            deduped.append(f)
    return deduped

def try_multiple_zooms(tileset_id, layer_name, zooms, description, max_workers=18):
    """Try multiple zoom levels and combine results for best coverage."""
    all_features = []
    for z in zooms:
        features = extract_tileset(tileset_id, layer_name, z, f"{description} (z{z})", max_workers)
        all_features.extend(features)
        if features:
            print(f"  Found {len(features)} features at z{z}")
    return all_features


def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    
    # ========================================
    # 1. Extract Top Labels
    # ========================================
    print("\n" + "="*70)
    print("PHASE 1: Top-level region labels")
    print("="*70)
    
    # Try z5 first, then z4, z6 for more coverage
    top_features = []
    for z in [5, 4, 6, 3]:
        feats = extract_tileset("martinvonwyss.3oqku56u", "m_top_labels-9isyyt", z,
                               f"Top labels z{z}", max_workers=18)
        top_features.extend(feats)
        if feats:
            break  # If we found features, no need to try more zooms
    
    if not top_features:
        # Try z7-z10
        for z in [7, 8, 9, 10]:
            feats = extract_tileset("martinvonwyss.3oqku56u", "m_top_labels-9isyyt", z,
                                   f"Top labels z{z}", max_workers=18)
            top_features.extend(feats)
            if feats:
                break
    
    top_deduped = dedup_label_features(top_features)
    print(f"\nTop labels: {len(top_features)} raw → {len(top_deduped)} deduped")
    
    # Show property keys
    if top_deduped:
        all_keys = set()
        for f in top_deduped:
            all_keys.update(f['properties'].keys())
        print(f"  Property keys: {sorted(all_keys)}")
        print(f"  Sample: {json.dumps(top_deduped[0]['properties'], indent=2)[:500]}")
    
    # Save
    with open('labels_top.json', 'w') as f:
        json.dump({'type': 'FeatureCollection', 'features': top_deduped}, f)
    print(f"  Saved labels_top.json ({len(top_deduped)} features)")
    
    # ========================================
    # 2. Extract Sub-region Labels
    # ========================================
    print("\n" + "="*70)
    print("PHASE 2: Sub-region labels")
    print("="*70)
    
    sub_features = []
    for z in [5, 6, 4, 7, 3, 8]:
        feats = extract_tileset("martinvonwyss.8bnug1jj", "m_sbrgn_labels-afx25k", z,
                               f"Sub-region labels z{z}", max_workers=18)
        sub_features.extend(feats)
        if feats:
            break
    
    sub_deduped = dedup_label_features(sub_features)
    print(f"\nSub-region labels: {len(sub_features)} raw → {len(sub_deduped)} deduped")
    
    if sub_deduped:
        all_keys = set()
        for f in sub_deduped:
            all_keys.update(f['properties'].keys())
        print(f"  Property keys: {sorted(all_keys)}")
        print(f"  Sample: {json.dumps(sub_deduped[0]['properties'], indent=2)[:500]}")
    
    with open('labels_subregion.json', 'w') as f:
        json.dump({'type': 'FeatureCollection', 'features': sub_deduped}, f)
    print(f"  Saved labels_subregion.json ({len(sub_deduped)} features)")
    
    # ========================================
    # 3. Extract Middle-level Labels
    # ========================================
    print("\n" + "="*70)
    print("PHASE 3: Middle-level labels")
    print("="*70)
    
    mid_features = []
    for z in [5, 6, 4, 7, 3, 8]:
        feats = extract_tileset("martinvonwyss.b8gzaigx", "Lbls_middle-764v5m", z,
                               f"Middle labels z{z}", max_workers=18)
        mid_features.extend(feats)
        if feats:
            break
    
    mid_deduped = dedup_label_features(mid_features)
    print(f"\nMiddle labels: {len(mid_features)} raw → {len(mid_deduped)} deduped")
    
    if mid_deduped:
        all_keys = set()
        for f in mid_deduped:
            all_keys.update(f['properties'].keys())
        print(f"  Property keys: {sorted(all_keys)}")
        print(f"  Sample: {json.dumps(mid_deduped[0]['properties'], indent=2)[:500]}")
    
    with open('labels_middle.json', 'w') as f:
        json.dump({'type': 'FeatureCollection', 'features': mid_deduped}, f)
    print(f"  Saved labels_middle.json ({len(mid_deduped)} features)")
    
    # ========================================
    # 4. Extract Köppen Climate Zones
    # ========================================
    print("\n" + "="*70)
    print("PHASE 4: Köppen climate zones")
    print("="*70)
    
    koppen_features = []
    for z in [5, 4, 3]:
        feats = extract_tileset("martinvonwyss.51oes0lc", "Koeppen_5m_WM-9q9klu", z,
                               f"Köppen climate z{z}", max_workers=18)
        koppen_features.extend(feats)
        if feats:
            break
    
    print(f"\nKöppen features: {len(koppen_features)}")
    
    if koppen_features:
        all_keys = set()
        for f in koppen_features:
            all_keys.update(f['properties'].keys())
        print(f"  Property keys: {sorted(all_keys)}")
        print(f"  Geometry types: {set(f['geometry']['type'] for f in koppen_features)}")
        print(f"  Sample: {json.dumps(koppen_features[0]['properties'], indent=2)[:500]}")
    
    with open('koppen_climate.geojson', 'w') as f:
        json.dump({'type': 'FeatureCollection', 'features': koppen_features}, f)
    print(f"  Saved koppen_climate.geojson ({len(koppen_features)} features)")
    
    # ========================================
    # 5. Merge label metadata into polygons
    # ========================================
    print("\n" + "="*70)
    print("PHASE 5: Merge label metadata into polygons.json")
    print("="*70)
    
    merge_labels_into_polygons(top_deduped, mid_deduped, sub_deduped)
    
    print("\n" + "="*70)
    print("ALL DONE!")
    print("="*70)


def merge_labels_into_polygons(top_labels, mid_labels, sub_labels):
    """Merge label metadata into polygons.json."""
    
    # Load polygons
    with open('polygons.json', 'r') as f:
        polygons = json.load(f)
    
    # Get current polygon property keys
    poly_keys = set()
    for feat in polygons['features']:
        poly_keys.update(feat['properties'].keys())
    print(f"Current polygon property keys: {sorted(poly_keys)}")
    
    # Combine all label features and build lookup by Name_rgn + country
    all_labels = top_labels + mid_labels + sub_labels
    
    # Get all label property keys
    label_keys = set()
    for f in all_labels:
        label_keys.update(f['properties'].keys())
    print(f"All label property keys: {sorted(label_keys)}")
    
    # Find keys that labels have but polygons don't
    new_keys = label_keys - poly_keys
    print(f"NEW keys from labels (not in polygons): {sorted(new_keys)}")
    
    # Also check keys that exist in both but might have empty values in polygons
    shared_keys = label_keys & poly_keys
    print(f"Shared keys: {sorted(shared_keys)}")
    
    # Build label lookup: Name_rgn + country → merged properties
    label_lookup = {}
    for f in all_labels:
        props = f['properties']
        name = props.get('Name_rgn', '')
        country = props.get('country', '')
        if not name:
            continue
        key = (name, country)
        if key not in label_lookup:
            label_lookup[key] = {}
        # Merge - keep non-empty values
        for k, v in props.items():
            if v is not None and v != '' and v != 0:
                if k not in label_lookup[key] or label_lookup[key][k] in (None, '', 0):
                    label_lookup[key][k] = v
    
    print(f"Label lookup has {len(label_lookup)} unique Name_rgn+country combos")
    
    # Merge into polygons
    enriched_count = 0
    fields_added = defaultdict(int)
    
    for feat in polygons['features']:
        props = feat['properties']
        name = props.get('Name_rgn', '')
        country = props.get('country', '')
        key = (name, country)
        
        if key in label_lookup:
            label_props = label_lookup[key]
            for k, v in label_props.items():
                if k in ('Name_rgn', 'country', 'OBJECTID', 'OBJECTID_1'):
                    continue  # Skip ID/key fields
                current = props.get(k)
                if current is None or current == '' or current == 0:
                    props[k] = v
                    fields_added[k] += 1
            enriched_count += 1
    
    print(f"\nEnriched {enriched_count} / {len(polygons['features'])} polygon features")
    print(f"Fields added/updated:")
    for k, count in sorted(fields_added.items(), key=lambda x: -x[1]):
        print(f"  {k}: {count} features")
    
    # Show sample enriched feature
    for feat in polygons['features']:
        if feat['properties'].get('climate') or feat['properties'].get('style'):
            print(f"\nSample enriched feature:")
            print(f"  {json.dumps(feat['properties'], indent=2)[:600]}")
            break
    
    # Save updated polygons
    with open('polygons.json', 'w') as f:
        json.dump(polygons, f)
    print(f"\nSaved updated polygons.json")
    
    # Update polygons_lite_v3.json 
    # Keep all property keys (including new ones from labels) but strip heavy geometry
    with open('polygons_lite_v3.json', 'r') as f:
        lite = json.load(f)
    
    # Build lookup from full polygons for metadata enrichment
    poly_lookup = {}
    for feat in polygons['features']:
        p = feat['properties']
        key = (p.get('Name_rgn', ''), p.get('country', ''))
        poly_lookup[key] = p
    
    lite_enriched = 0
    for feat in lite['features']:
        p = feat['properties']
        key = (p.get('Name_rgn', ''), p.get('country', ''))
        if key in poly_lookup:
            full_props = poly_lookup[key]
            for k, v in full_props.items():
                if k not in p or p[k] in (None, '', 0):
                    if v not in (None, '', 0):
                        p[k] = v
                        lite_enriched += 1
    
    with open('polygons_lite_v3.json', 'w') as f:
        json.dump(lite, f)
    print(f"Updated polygons_lite_v3.json (enriched {lite_enriched} field values)")


if __name__ == '__main__':
    main()
