#!/usr/bin/env python3
"""
Fix wine regions with wildly incorrect polygon boundaries.

Problem: 143 regions have polygons spanning >5° lat/lng — they're using
parent state/country boundaries instead of actual wine region boundaries.

Fix: Replace bad polygons with a properly-sized approximate polygon
(octagon) centered on the region's known lat/lng coordinates.
Size is based on the region's classification.
"""
import json
import math

INPUT = 'polygons.json'
OUTPUT = 'polygons.json'  # overwrite in place
MAX_SPAN_DEG = 5.0  # flag anything wider than this

# Approximate radius (in degrees) by classification
RADIUS_BY_CLASS = {
    'American Viticultural Area': 0.25,
    'Geographical Indication': 0.5,
    'Appellation': 0.3,
    'Denominación de Origen': 0.3,
    'Denominazione di Origine': 0.3,
    'Appellation d\'Origine Contrôlée': 0.25,
    'Appellation d\'Origine Protégée': 0.25,
    'Wine of Origin': 0.3,
    'Protected Designation of Origin': 0.3,
    'Quality Wine Region': 0.3,
    'Wine Region': 0.4,
    'Wine Zone': 0.6,
    'Wine District': 0.2,
    'Super Zone': 0.8,
    'Zone': 0.4,
    'Registered Geographical Indication': 0.5,
}
DEFAULT_RADIUS = 0.3


def make_octagon(lat, lng, radius_deg):
    """Generate an octagon polygon centered on lat/lng."""
    points = []
    for i in range(8):
        angle = math.pi / 4 * i
        # Adjust lng radius for latitude (degrees get narrower toward poles)
        lng_radius = radius_deg / math.cos(math.radians(lat)) if abs(lat) < 85 else radius_deg
        plng = lng + lng_radius * math.cos(angle)
        plat = lat + radius_deg * math.sin(angle)
        points.append([round(plng, 4), round(plat, 4)])
    points.append(points[0])  # close the ring
    return points


def get_span(feature):
    """Get lat/lng span of a feature's geometry."""
    geom = feature.get('geometry', {})
    gtype = geom.get('type', '')
    coords = geom.get('coordinates', [])
    
    all_lats = []
    all_lngs = []
    if gtype == 'Polygon':
        for ring in coords:
            for c in ring:
                all_lngs.append(c[0])
                all_lats.append(c[1])
    elif gtype == 'MultiPolygon':
        for poly in coords:
            for ring in poly:
                for c in ring:
                    all_lngs.append(c[0])
                    all_lats.append(c[1])
    
    if not all_lats:
        return 0, 0
    return max(all_lats) - min(all_lats), max(all_lngs) - min(all_lngs)


def main():
    with open(INPUT) as f:
        data = json.load(f)
    
    fixed = 0
    skipped = 0
    
    for feature in data['features']:
        props = feature.get('properties', {})
        lat_span, lng_span = get_span(feature)
        
        if lat_span <= MAX_SPAN_DEG and lng_span <= MAX_SPAN_DEG:
            continue  # polygon is fine
        
        name = props.get('Name_rgn', 'Unknown')
        lat = props.get('latitude')
        lng = props.get('longitude')
        classification = props.get('class', '')
        
        if lat is None or lng is None:
            print(f"SKIP (no center coords): {name}")
            skipped += 1
            continue
        
        try:
            lat = float(lat)
            lng = float(lng)
        except (ValueError, TypeError):
            print(f"SKIP (bad coords): {name} lat={lat} lng={lng}")
            skipped += 1
            continue
        
        # Determine radius based on classification
        radius = RADIUS_BY_CLASS.get(classification, DEFAULT_RADIUS)
        
        # For very large parent regions (zones/super zones), use bigger radius
        has_children = props.get('has_children', False)
        child_count = props.get('child_count', 0)
        if has_children and child_count and int(child_count) > 5:
            radius = max(radius, 0.8)
        elif has_children:
            radius = max(radius, 0.5)
        
        # Generate replacement polygon
        octagon = make_octagon(lat, lng, radius)
        feature['geometry'] = {
            'type': 'Polygon',
            'coordinates': [octagon]
        }
        
        # Update bbox in properties if present
        new_lat_span = radius * 2
        new_lng_span = radius * 2 / math.cos(math.radians(lat)) if abs(lat) < 85 else radius * 2
        
        print(f"FIXED: {name} ({classification}) — was {lat_span:.1f}°×{lng_span:.1f}° → octagon r={radius}° at ({lat:.2f}, {lng:.2f})")
        fixed += 1
    
    # Write output
    with open(OUTPUT, 'w') as f:
        json.dump(data, f)
    
    print(f"\n{'='*60}")
    print(f"Fixed: {fixed}")
    print(f"Skipped: {skipped}")
    print(f"Total features: {len(data['features'])}")
    
    # Verify
    bad_after = 0
    for feature in data['features']:
        lat_span, lng_span = get_span(feature)
        if lat_span > MAX_SPAN_DEG or lng_span > MAX_SPAN_DEG:
            bad_after += 1
    print(f"Bad polygons remaining: {bad_after}")


if __name__ == '__main__':
    main()
