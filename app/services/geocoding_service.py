from typing import Tuple, Dict, Optional
import re


class GeocodingService:
    """
    Resolves location queries to geographic coordinates [latitude, longitude].
    Supports named locations (common parametric insurance agricultural zones)
    and raw 'lat,lon' coordinates.
    """

    PRESET_COORDINATES: Dict[str, Tuple[float, float]] = {
        "gujarat/ahmedabad": (23.0225, 72.5714),
        "ahmedabad": (23.0225, 72.5714),
        "gujarat": (22.2587, 71.1924),
        "maharashtra/mumbai": (19.0760, 72.8777),
        "maharashtra/pune": (18.5204, 73.8567),
        "punjab/ludhiana": (30.9010, 75.8573),
        "delhi": (28.6139, 77.2090),
        "bengaluru": (12.9716, 77.5946),
        "kenya/nairobi": (-1.2921, 36.8219),
        "kenya/turkana": (3.1167, 35.6000),
        "us/iowa": (41.8780, -93.0977),
        "us/kansas": (39.0119, -98.4842),
    }

    @classmethod
    def resolve_coordinates(
        cls,
        location_str: str,
        lat_override: Optional[float] = None,
        lon_override: Optional[float] = None
    ) -> Tuple[float, float]:
        if lat_override is not None and lon_override is not None:
            return lat_override, lon_override

        clean_loc = location_str.strip().lower()

        # Check presets
        if clean_loc in cls.PRESET_COORDINATES:
            return cls.PRESET_COORDINATES[clean_loc]

        # Check for 'lat,lon' format (e.g. "23.02, 72.57")
        match = re.match(r"^[-+]?([1-8]?\d(\.\d+)?|90(\.0+)?),\s*[-+]?(180(\.0+)?|((1[0-7]\d)|([1-9]?\d))(\.\d+)?)$", clean_loc)
        if match:
            parts = [float(p.strip()) for p in clean_loc.split(",")]
            return parts[0], parts[1]

        # Default fallback (Ahmedabad / Gujarat  from seed request)
        return (23.0225, 72.5714)
