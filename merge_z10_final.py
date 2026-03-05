#!/usr/bin/env python3
"""Merge z10 geometry with old metadata. Simple, no-nonsense version."""
import json, os

base = os.path.dirname(os.path.abspath(__file__))

ALL_REQUIRED = [
    'Name_rgn', 'country', 'class', 'state', 'altname', 'official', 'gen',
    'latitude', 'color', 'longitude', 'Shape_Leng',
    'GDD_1', 'ppt_yr', 'ppt_gs', 'zoom_level', 'has_children',
    'child_count', 'show_max_zoom', 'show_min_zoom'
]
DEFAULTS = {
    'GDD_1': 0, 'ppt_yr': 0, 'ppt_gs': 0,
    'zoom_level': 3, 'has_children': False, 'child_count': 0,
    'show_max_zoom': 99, 'show_min_zoom': 7,
    'Shape_Leng': 0, 'longitude': 0,
    'state': '', 'altname': '', 'official': 1, 'gen': 1,
    'latitude': 0, 'color': 1, 'class': '',
    'Name_rgn': 'Unknown', 'country': 'Unknown',
}

def round_coords(coords, p):
    if isinstance(coords, float): return round(coords, p)
    if isinstance(coords, int): return coords
    if isinstance(coords, list): return [round_coords(c, p) for c in coords]
    return coords

def count_pts(coords):
    if not coords: return 0
    if isinstance(coords[0], (int, float)): return 1
    return sum(count_pts(c) for c in coords)

# Load
print("Loading...")
with open(os.path.join(base, 'polygons.json')) as f:
    old_data = json.load(f)
with open(os.path.join(base, 'polygons_z10_lite.json')) as f:
    z10_lite = json.load(f)
with open(os.path.join(base, 'polygons_z10_web.json')) as f:
    z10_web = json.load(f)

print(f"  Old: {len(old_data['features'])} | z10_lite: {len(z10_lite['features'])} | z10_web: {len(z10_web['features'])}")

# Build old lookup
old_lookup = {}
for feat in old_data['features']:
    p = feat['properties']
    key = (p['Name_rgn'].lower(), p['country'].lower())
    if key not in old_lookup:
        old_lookup[key] = p
    else:
        existing = old_lookup[key]
        es = sum(1 for v in existing.values() if v is not None and v != '' and v != 0)
        ns = sum(1 for v in p.values() if v is not None and v != '' and v != 0)
        if ns > es:
            old_lookup[key] = p

def merge(z10_data, precision, label):
    features = []
    matched = unmatched = 0
    for feat in z10_data['features']:
        z10_props = feat['properties']
        key = (z10_props['Name_rgn'].lower(), z10_props['country'].lower())
        old_props = old_lookup.get(key)
        
        if old_props:
            matched += 1
            # Start from OLD props (authoritative metadata), keep z10 name casing
            props = dict(old_props)
            props['Name_rgn'] = z10_props['Name_rgn']
            props['country'] = z10_props['country']
        else:
            unmatched += 1
            props = dict(z10_props)
        
        # Guarantee ALL required fields exist
        for field in ALL_REQUIRED:
            if field not in props or props[field] is None:
                props[field] = DEFAULTS[field]
        
        # Clean extra z10 fields
        for extra in ['climate', 'style']:
            props.pop(extra, None)
        
        geom = feat['geometry']
        features.append({
            'type': 'Feature',
            'properties': props,
            'geometry': {
                'type': geom['type'],
                'coordinates': round_coords(geom['coordinates'], precision)
            }
        })
    
    result = {'type': 'FeatureCollection', 'features': features}
    
    # Verify
    total_pts = sum(count_pts(f['geometry']['coordinates']) for f in features)
    missing_any = 0
    for f in features:
        for field in ALL_REQUIRED:
            if field not in f['properties']:
                missing_any += 1
                break
    
    print(f"\n  {label}: {len(features)} features, {matched} matched, {unmatched} unmatched, {total_pts:,} pts, {missing_any} incomplete")
    return result

print("\nMerging...")
web = merge(z10_web, 5, "Web")
mobile = merge(z10_lite, 3, "Mobile")

# Write
web_path = os.path.join(base, 'polygons_z10_merged.json')
mobile_path = os.path.join(base, 'polygons_z10_mobile.json')

print("\nWriting...")
with open(web_path, 'w') as f:
    json.dump(web, f, separators=(',', ':'))
print(f"  {web_path}: {os.path.getsize(web_path)/1024/1024:.1f} MB")

with open(mobile_path, 'w') as f:
    json.dump(mobile, f, separators=(',', ':'))
print(f"  {mobile_path}: {os.path.getsize(mobile_path)/1024/1024:.1f} MB")

print("\n✅ Done!")
