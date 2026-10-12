"""
Distance on the ground.

One implementation, because there were about to be two: field job
assessments already measure how far the technician was from the site, and
attendance now asks the same question about the office. Two copies of a
haversine drift apart in the third decimal place and nobody notices until
somebody is refused a clock-in by a formula that disagrees with the one on
the review screen.
"""
from math import atan2, cos, radians, sin, sqrt

EARTH_RADIUS_M = 6371000


def metres_between(lat1, lon1, lat2, lon2):
    """
    Great-circle distance in whole metres, or None if any part is missing.

    Takes anything float() accepts, because coordinates arrive as Decimal
    from the database, as str from a form and as float from a phone.
    """
    try:
        lat1, lon1, lat2, lon2 = (float(v) for v in (lat1, lon1, lat2, lon2))
    except (TypeError, ValueError):
        return None

    phi1, phi2 = radians(lat1), radians(lat2)
    dphi = radians(lat2 - lat1)
    dlambda = radians(lon2 - lon1)
    a = sin(dphi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(dlambda / 2) ** 2
    return round(EARTH_RADIUS_M * 2 * atan2(sqrt(a), sqrt(1 - a)))
