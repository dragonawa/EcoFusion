"""EcoFusion: generic limited-budget panorama search planner."""

import math

EARTH_RADIUS = 6378137.0


def plan_parcel_search(geometry, max_points=5, offset_m=20):
    """Plan panorama search points around a cadastral polygon.

    Input: WGS84 polygon_latlon and centroid_latlon.
    Output: search coordinates and a viewing target.

    Does not claim that waste is located at the centroid.
    """
    if not isinstance(geometry, dict):
        raise ValueError("Geometry must be a dictionary")

    if max_points < 1 or max_points > 5:
        raise ValueError("Search budget must be 1..5")

    polygon = geometry.get("polygon_latlon")
    center = geometry.get("centroid_latlon")

    if not isinstance(polygon, list) or len(polygon) < 4:
        raise ValueError("Invalid polygon")

    if not isinstance(center, list) or len(center) != 2:
        raise ValueError("Invalid centroid")

    lat0, lon0 = map(float, center)

    if not (-90 < lat0 < 90 and -180 < lon0 < 180):
        raise ValueError("Invalid geographic center")

    coslat = math.cos(math.radians(lat0))

    if abs(coslat) < 0.01:
        raise ValueError("Unsupported polar geometry")

    vertices = []

    for point in polygon:
        lat, lon = map(float, point)

        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError("Invalid polygon coordinate")

        x = math.radians(lon-lon0)*EARTH_RADIUS*coslat
        y = math.radians(lat-lat0)*EARTH_RADIUS
        vertices.append((x,y))

    def to_wgs84(x,y):
        return [
            lat0 + math.degrees(y/EARTH_RADIUS),
            lon0 + math.degrees(x/(EARTH_RADIUS*coslat))
        ]

    west = min(x for x,y in vertices)
    east = max(x for x,y in vertices)
    south = min(y for x,y in vertices)
    north = max(y for x,y in vertices)

    width = east-west
    height = north-south

    if width < 1 or height < 1:
        raise ValueError("Degenerate parcel bounds")

    # First request targets the parcel center.
    # Other requests sample the four outer approaches.
    locations = [
        ("center", 0.0, 0.0),
        ("north", 0.0, north+offset_m),
        ("south", 0.0, south-offset_m),
        ("east", east+offset_m, 0.0),
        ("west", west-offset_m, 0.0)
    ]

    locations = locations[:max_points]

    points = [
        {
            "role": name,
            "coordinates": to_wgs84(x,y),
            "view_target": [lat0,lon0]
        }
        for name,x,y in locations
    ]

    return {
        "mode": "parcel_geometry",
        "cadastral_number": geometry.get("cadastral_number"),
        "search_budget": max_points,
        "search_points": points,
        "parcel_center": [lat0,lon0],
        "parcel_width_m": round(width,1),
        "parcel_height_m": round(height,1),
        "orientation_basis": "parcel_geometry",
        "incident_location_confirmed": False,
        "note": (
            "Search points and viewing target are geometric "
            "hypotheses, not confirmed waste locations."
        )
    }
