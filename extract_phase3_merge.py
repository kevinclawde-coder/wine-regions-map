#!/usr/bin/env python3
"""Phase 3: Merge all zoom-level extractions, pick best polygon per region.
Then merge with existing polygons.json, dedup, and create final output."""

import json
import os
import time
from shapely.geometry import shape, mapping, MultiPolygon, Polygon
from collections import defaultdict

def load_geojson(path):
    if not os.path.exists(path):
        print(f"  Missing: {path}")
        return []
    with open(path) as f:
        data = json.load(f)
    features = data.get('features', [])
    print(f"  Loaded {path}: {len(features)} features")
    return features

def count_coords(geom):
    """Count total coordinate points in a geometry."""
    count = 0
    if geom['type'] == 'Polygon':
        for ring in geom['coordinates']:
            count += len(ring)
    elif geom['type'] == 'MultiPolygon':
        for poly in geom['coordinates']:
            for ring in poly:
                count += len(ring)
    return count

def simplify_geometry(geom, tolerance):
    """Simplify a geometry using Douglas-Peucker."""
    try:
        s = shape(geom)
        simplified = s.simplify(tolerance, preserve_topology=True)
        if simplified.is_empty:
            return geom
        if isinstance(simplified, Polygon):
            simplified = MultiPolygon([simplified])
        return mapping(simplified)
    except:
        return geom

def round_coords(geom, decimals=5):
    """Round coordinates to N decimal places to save space."""
    def round_ring(ring):
        return [[round(c, decimals) for c in pt] for pt in ring]
    
    if geom['type'] == 'Polygon':
        return {'type': 'Polygon', 'coordinates': [round_ring(r) for r in geom['coordinates']]}
    elif geom['type'] == 'MultiPolygon':
        return {'type': 'MultiPolygon', 'coordinates': [[round_ring(r) for r in poly] for poly in geom['coordinates']]}
    return geom

if __name__ == '__main__':
    start = time.time()
    
    print("=== Loading all extractions ===")
    
    # Load all zoom levels for both tilesets
    middle_z5 = load_geojson('middle_regions_z5.json')
    middle_z6 = load_geojson('middle_regions_z6.json')
    middle_z7 = load_geojson('middle_regions_z7.json')
    top_z5 = load_geojson('top_regions_z5.json')
    top_z6 = load_geojson('top_regions_z6.json')
    top_z7 = load_geojson('top_regions_z7.json')
    
    print("\n=== Selecting best polygon per region (highest zoom = most detail) ===")
    
    # For each tileset, prefer higher zoom level polygons
    # Key: Name_rgn|country
    
    def best_per_region(zoom_levels):
        """Given list of (zoom, features), pick highest zoom per region."""
        best = {}
        for zoom, features in sorted(zoom_levels, key=lambda x: x[0]):  # Process low zoom first
            for f in features:
                name = f['properties'].get('Name_rgn', 'unknown')
                country = f['properties'].get('country', 'unknown')
                key = f"{name}|{country}"
                # Always overwrite with higher zoom (more detail)
                best[key] = f
        return list(best.values())
    
    middle_best = best_per_region([
        (5, middle_z5),
        (6, middle_z6),
        (7, middle_z7),
    ])
    print(f"Middle tileset best: {len(middle_best)} unique regions")
    
    top_best = best_per_region([
        (5, top_z5),
        (6, top_z6),
        (7, top_z7),
    ])
    print(f"Top tileset best: {len(top_best)} unique regions")
    
    # Save raw extraction files
    with open('middle_regions_raw.json', 'w') as f:
        json.dump({'type': 'FeatureCollection', 'features': middle_best}, f)
    print(f"Saved middle_regions_raw.json")
    
    with open('top_regions_raw.json', 'w') as f:
        json.dump({'type': 'FeatureCollection', 'features': top_best}, f)
    print(f"Saved top_regions_raw.json")
    
    print("\n=== Merging new extractions with existing polygons.json ===")
    
    # Load existing
    existing = load_geojson('polygons.json')
    
    # Build the merged set: existing has priority for regions that already exist
    # Key by Name_rgn|country
    merged = {}
    
    # Add existing first (these have priority, especially NZ from OSM)
    existing_names = set()
    for f in existing:
        name = f['properties'].get('Name_rgn', 'unknown')
        country = f['properties'].get('country', 'unknown')
        key = f"{name}|{country}"
        merged[key] = f
        existing_names.add(key)
    
    print(f"Existing regions: {len(existing_names)}")
    
    # Add top tileset (parent/larger regions)
    new_from_top = 0
    for f in top_best:
        name = f['properties'].get('Name_rgn', 'unknown')
        country = f['properties'].get('country', 'unknown')
        key = f"{name}|{country}"
        if key not in merged:
            merged[key] = f
            new_from_top += 1
    
    print(f"New from top tileset: {new_from_top}")
    
    # Add middle tileset (mid-level regions — the big gap)
    new_from_middle = 0
    for f in middle_best:
        name = f['properties'].get('Name_rgn', 'unknown')
        country = f['properties'].get('country', 'unknown')
        key = f"{name}|{country}"
        if key not in merged:
            merged[key] = f
            new_from_middle += 1
    
    print(f"New from middle tileset: {new_from_middle}")
    print(f"Total merged: {len(merged)} regions")
    
    # Convert to feature list
    all_features = list(merged.values())
    
    # Save full merged version
    merged_geojson = {'type': 'FeatureCollection', 'features': all_features}
    with open('polygons_merged.json', 'w') as f:
        json.dump(merged_geojson, f)
    size_mb = os.path.getsize('polygons_merged.json') / 1024 / 1024
    print(f"Saved polygons_merged.json ({len(all_features)} features, {size_mb:.1f} MB)")
    
    print("\n=== Creating simplified versions ===")
    
    # Simplify for web (tolerance ~0.001 degrees ≈ ~100m)
    web_features = []
    for f in all_features:
        simplified_geom = simplify_geometry(f['geometry'], 0.001)
        rounded_geom = round_coords(simplified_geom, 5)
        
        # Clean up properties - keep only useful ones
        props = {}
        for k in ['Name_rgn', 'country', 'class', 'official', 'gen', 'state',
                   'latitude', 'color', 'altname', 'climate', 'style',
                   'GDD_1', 'ppt_yr', 'ppt_gs']:
            if k in f['properties']:
                props[k] = f['properties'][k]
        
        web_features.append({
            'type': 'Feature',
            'properties': props,
            'geometry': rounded_geom
        })
    
    web_geojson = {'type': 'FeatureCollection', 'features': web_features}
    with open('polygons_final.json', 'w') as f:
        json.dump(web_geojson, f)
    size_mb = os.path.getsize('polygons_final.json') / 1024 / 1024
    print(f"Saved polygons_final.json ({len(web_features)} features, {size_mb:.1f} MB)")
    
    # Create lite version for Flutter (more aggressive simplification)
    lite_features = []
    for f in all_features:
        simplified_geom = simplify_geometry(f['geometry'], 0.005)
        rounded_geom = round_coords(simplified_geom, 4)
        
        # Minimal properties for Flutter
        props = {}
        for k in ['Name_rgn', 'country', 'class', 'official', 'gen', 'state',
                   'latitude', 'color', 'altname']:
            if k in f['properties']:
                props[k] = f['properties'][k]
        
        # Skip tiny polygons
        try:
            s = shape(rounded_geom)
            if s.area < 1e-8:
                continue
        except:
            pass
        
        lite_features.append({
            'type': 'Feature',
            'properties': props,
            'geometry': rounded_geom
        })
    
    lite_geojson = {'type': 'FeatureCollection', 'features': lite_features}
    with open('polygons_lite_v3.json', 'w') as f:
        json.dump(lite_geojson, f)
    size_mb = os.path.getsize('polygons_lite_v3.json') / 1024 / 1024
    print(f"Saved polygons_lite_v3.json ({len(lite_features)} features, {size_mb:.1f} MB)")
    
    print("\n=== Quality Checks ===")
    
    # Country breakdown
    countries = defaultdict(int)
    for f in web_features:
        c = f['properties'].get('country', 'unknown')
        countries[c] += 1
    
    print(f"\nRegions by country (all {len(countries)} countries):")
    for c, n in sorted(countries.items(), key=lambda x: -x[1]):
        print(f"  {c}: {n}")
    
    # Check for key regions
    region_names = {f['properties'].get('Name_rgn', '') for f in web_features}
    
    checks = {
        'France': ['Champagne', 'Burgundy', 'Bordeaux', 'Loire Valley', 'Alsace'],
        'Italy': ['Chianti', 'Barolo', 'Brunello di Montalcino', 'Prosecco', 'Amarone'],
        'US': ['Napa Valley', 'Sonoma Coast', 'Willamette Valley', 'Paso Robles'],
        'Australia': ['Margaret River', 'Barossa Valley', 'Yarra Valley', 'Hunter Valley'],
    }
    
    print(f"\nKey region checks:")
    for area, names in checks.items():
        print(f"  {area}:")
        for name in names:
            # Try exact and partial match
            found = name in region_names
            if not found:
                partial = [r for r in region_names if name.lower() in r.lower()]
                if partial:
                    print(f"    ✓ {name} (as '{partial[0]}')")
                else:
                    print(f"    ✗ {name} NOT FOUND")
            else:
                print(f"    ✓ {name}")
    
    # Coordinate validation
    bad_coords = 0
    for f in web_features:
        geom = f['geometry']
        coords = []
        if geom['type'] == 'Polygon':
            for ring in geom['coordinates']:
                coords.extend(ring)
        elif geom['type'] == 'MultiPolygon':
            for poly in geom['coordinates']:
                for ring in poly:
                    coords.extend(ring)
        for c in coords:
            if len(c) >= 2 and (c[1] < -90 or c[1] > 90 or c[0] < -180 or c[0] > 180):
                bad_coords += 1
                break
    
    print(f"\nCoordinate validation: {bad_coords} features with out-of-range coords")
    
    elapsed = time.time() - start
    print(f"\nPhase 3 complete in {elapsed:.1f}s")
    print(f"Total regions: {len(web_features)} (was {len(existing)}, added {len(web_features) - len(existing)})")
