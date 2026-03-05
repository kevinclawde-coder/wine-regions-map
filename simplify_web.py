#!/usr/bin/env python3
"""Simplify z10 merged polygons for web using Douglas-Peucker.
Target: ~15-25MB (down from 94MB), visually identical at typical map zoom levels."""
import json, os
from shapely.geometry import shape, mapping
from shapely.validation import make_valid

base = os.path.dirname(os.path.abspath(__file__))

print("Loading polygons_z10_merged.json...")
with open(os.path.join(base, 'polygons_z10_merged.json')) as f:
    data = json.load(f)

print(f"  Features: {len(data['features'])}")

# Simplify with tolerance — in degrees:
# 0.001 ≈ 111m — too aggressive, loses small region detail
# 0.0005 ≈ 55m — good balance  
# 0.0002 ≈ 22m — high quality, bigger file
TOLERANCE = 0.0008  # ~88m — good balance for web (still 2.7x more detail than old z5-z7)

simplified_features = []
total_before = 0
total_after = 0
dropped = 0

for feat in data['features']:
    geom = feat['geometry']
    try:
        s = shape(geom)
        if not s.is_valid:
            s = make_valid(s)
        
        # Count points before
        if hasattr(s, 'exterior'):
            before = len(s.exterior.coords)
        else:
            before = sum(len(g.exterior.coords) for g in s.geoms if hasattr(g, 'exterior'))
        total_before += before
        
        simplified = s.simplify(TOLERANCE, preserve_topology=True)
        
        if simplified.is_empty:
            dropped += 1
            continue
            
        # Count points after
        if hasattr(simplified, 'exterior'):
            after = len(simplified.exterior.coords)
        else:
            after = sum(len(g.exterior.coords) for g in simplified.geoms if hasattr(g, 'exterior'))
        total_after += after
        
        # Convert back to GeoJSON and round to 4 decimals
        new_geom = mapping(simplified)
        
        def round_coords(coords, p):
            if isinstance(coords, float): return round(coords, p)
            if isinstance(coords, int): return coords
            if isinstance(coords, (list, tuple)): return [round_coords(c, p) for c in coords]
            return coords
        
        new_geom['coordinates'] = round_coords(list(new_geom['coordinates']), 4)
        
        simplified_features.append({
            'type': 'Feature',
            'properties': feat['properties'],
            'geometry': new_geom
        })
    except Exception as e:
        print(f"  Error on {feat['properties'].get('Name_rgn', '?')}: {e}")
        # Keep original
        simplified_features.append(feat)

result = {'type': 'FeatureCollection', 'features': simplified_features}

print(f"  Dropped (empty after simplify): {dropped}")
print(f"  Points: {total_before:,} → {total_after:,} ({total_after/max(total_before,1)*100:.1f}%)")

out_path = os.path.join(base, 'polygons.json')
print(f"\nWriting {out_path}...")
with open(out_path, 'w') as f:
    json.dump(result, f, separators=(',', ':'))
size = os.path.getsize(out_path)
print(f"  Size: {size/1024/1024:.1f} MB")
print(f"  Features: {len(simplified_features)}")
print("\n✅ Done!")
