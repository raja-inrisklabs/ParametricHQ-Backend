import datetime
import math
import hashlib
from typing import Any, Dict, List, Optional, Tuple
import pandas as pd
import numpy as np

from app.core.config import settings
from app.core.logging import logger
from app.core.exceptions import ProviderError
from app.schemas.dataset import DatasetRequest
from app.providers.base import BaseProvider


_TERMINAL_CDS_STATES = ("successful", "failed", "rejected", "completed")


def _cds_text(*args) -> str:
    """Turn a cdsapi/ecmwf callback into one line.

    ecmwf passes (level, message). cdsapi passes a printf template plus values.
    """
    if not args:
        return ""
    parts = args[1:] if isinstance(args[0], int) else args
    if not parts:
        return ""
    template = parts[0] if isinstance(parts[0], str) else str(parts[0])
    try:
        return template % parts[1:] if len(parts) > 1 else template
    except (TypeError, ValueError):
        return " ".join(str(part) for part in parts)


class _CdsStatusLog:
    """CDS polls the same state for minutes. Keep one line per state change."""

    def __init__(self) -> None:
        self._last: Optional[str] = None

    def __call__(self, *args) -> None:
        message = _cds_text(*args).strip()
        if not message or message == self._last:
            return
        self._last = message
        logger.info(message)
        if any(state in message for state in _TERMINAL_CDS_STATES):
            self._last = None


class ERA5Provider(BaseProvider):
    """
    ECMWF Copernicus ERA5 and ERA5-Land Provider.
    
    ERA5-Land provides enhanced spatial resolution of 9km (0.1° grid) for land-surface
    applications including agriculture, hydrology, and risk analysis.
    
    API access requires Copernicus Climate Data Store (CDS) credentials:
    CDSAPI_URL and CDSAPI_KEY in .env.
    
    When credentials are not yet configured, a realistic simulation mode
    generates physically consistent data for all variables to enable local
    testing, analysis, and pipeline verification.
    """

    # Comprehensive ERA5-Land & ERA5 Variable Catalog
    VARIABLES: Dict[str, Dict[str, Any]] = {
        # --- Temperature & Humidity ---
        "2m_temperature": {
            "cds_name": "2m_temperature",
            "unit": "degC",
            "category": "temperature",
            "dataset": "reanalysis-era5-land",
            "description": "Air temperature at 2 metres above the surface (converted from Kelvin to Celsius).",
        },
        "2m_dewpoint_temperature": {
            "cds_name": "2m_dewpoint_temperature",
            "unit": "degC",
            "category": "temperature",
            "dataset": "reanalysis-era5-land",
            "description": "Dewpoint temperature at 2 metres above the surface.",
        },
        "skin_temperature": {
            "cds_name": "skin_temperature",
            "unit": "degC",
            "category": "temperature",
            "dataset": "reanalysis-era5-land",
            "description": "Temperature of the surface at the interface with the atmosphere.",
        },

        # --- Precipitation & Hydrology ---
        "total_precipitation": {
            "cds_name": "total_precipitation",
            "unit": "mm",
            "category": "hydrology",
            "dataset": "reanalysis-era5-land",
            "description": "Accumulated liquid and frozen water comprising rain and snow falling to the surface (converted from m to mm).",
        },
        "total_evaporation": {
            "cds_name": "total_evaporation",
            "unit": "mm",
            "category": "hydrology",
            "dataset": "reanalysis-era5-land",
            "description": "Accumulated amount of water evaporated from soil and vegetation.",
        },
        "potential_evaporation": {
            "cds_name": "potential_evaporation",
            "unit": "mm",
            "category": "hydrology",
            "dataset": "reanalysis-era5-land",
            "description": "Theoretical maximum amount of water that would evaporate under unlimited water supply.",
        },
        "surface_runoff": {
            "cds_name": "surface_runoff",
            "unit": "mm",
            "category": "hydrology",
            "dataset": "reanalysis-era5-land",
            "description": "Water flowing over the land surface directly into drainage channels.",
        },
        "sub_surface_runoff": {
            "cds_name": "sub_surface_runoff",
            "unit": "mm",
            "category": "hydrology",
            "dataset": "reanalysis-era5-land",
            "description": "Water draining slowly through soil and sub-surface rock.",
        },

        # --- Soil Moisture (Volumetric Water Content: 4 Layers) ---
        "volumetric_soil_water_layer_1": {
            "cds_name": "volumetric_soil_water_layer_1",
            "unit": "m3/m3",
            "category": "soil_moisture",
            "dataset": "reanalysis-era5-land",
            "description": "Volume of water in topsoil layer 1 (0 to 7 cm depth).",
        },
        "volumetric_soil_water_layer_2": {
            "cds_name": "volumetric_soil_water_layer_2",
            "unit": "m3/m3",
            "category": "soil_moisture",
            "dataset": "reanalysis-era5-land",
            "description": "Volume of water in shallow root zone layer 2 (7 to 28 cm depth).",
        },
        "volumetric_soil_water_layer_3": {
            "cds_name": "volumetric_soil_water_layer_3",
            "unit": "m3/m3",
            "category": "soil_moisture",
            "dataset": "reanalysis-era5-land",
            "description": "Volume of water in deep root zone layer 3 (28 to 100 cm depth).",
        },
        "volumetric_soil_water_layer_4": {
            "cds_name": "volumetric_soil_water_layer_4",
            "unit": "m3/m3",
            "category": "soil_moisture",
            "dataset": "reanalysis-era5-land",
            "description": "Volume of water in subsoil layer 4 (100 to 289 cm depth).",
        },

        # --- Soil Temperature (4 Levels) ---
        "soil_temperature_level_1": {
            "cds_name": "soil_temperature_level_1",
            "unit": "degC",
            "category": "soil_temperature",
            "dataset": "reanalysis-era5-land",
            "description": "Temperature of the soil in level 1 (0 to 7 cm).",
        },
        "soil_temperature_level_2": {
            "cds_name": "soil_temperature_level_2",
            "unit": "degC",
            "category": "soil_temperature",
            "dataset": "reanalysis-era5-land",
            "description": "Temperature of the soil in level 2 (7 to 28 cm).",
        },

        # --- Radiation & Energy Balance ---
        "surface_solar_radiation_downwards": {
            "cds_name": "surface_solar_radiation_downwards",
            "unit": "MJ/m2",
            "category": "radiation",
            "dataset": "reanalysis-era5-land",
            "description": "Amount of solar radiation reaching the surface (direct + diffuse).",
        },
        "surface_thermal_radiation_downwards": {
            "cds_name": "surface_thermal_radiation_downwards",
            "unit": "MJ/m2",
            "category": "radiation",
            "dataset": "reanalysis-era5-land",
            "description": "Longwave thermal radiation emitted by atmosphere towards the surface.",
        },

        # --- Wind ---
        "10m_wind_speed": {
            "cds_name": ["10m_u_component_of_wind", "10m_v_component_of_wind"],
            "unit": "m/s",
            "category": "wind",
            "dataset": "reanalysis-era5-land",
            "description": "Horizontal wind speed magnitude at 10 metres height.",
        },
        "10m_u_component_of_wind": {
            "cds_name": "10m_u_component_of_wind",
            "unit": "m/s",
            "category": "wind",
            "dataset": "reanalysis-era5-land",
            "description": "Eastward component of horizontal wind at 10 metres height.",
        },
        "10m_v_component_of_wind": {
            "cds_name": "10m_v_component_of_wind",
            "unit": "m/s",
            "category": "wind",
            "dataset": "reanalysis-era5-land",
            "description": "Northward component of horizontal wind at 10 metres height.",
        },

        # --- Snow & Vegetation ---
        "snow_depth": {
            "cds_name": "snow_depth",
            "unit": "m",
            "category": "snow",
            "dataset": "reanalysis-era5-land",
            "description": "Instantaneous snow thickness on the ground.",
        },
        "leaf_area_index_low_vegetation": {
            "cds_name": "leaf_area_index_low_vegetation",
            "unit": "m2/m2",
            "category": "vegetation",
            "dataset": "reanalysis-era5-land",
            "description": "Leaf area index for low vegetation (crops, grasses).",
        },
    }

    # Friendly aliases mapping to canonical variable keys
    ALIASES: Dict[str, str] = {
        "rainfall": "total_precipitation",
        "precipitation": "total_precipitation",
        "rain": "total_precipitation",
        "temperature": "2m_temperature",
        "temp": "2m_temperature",
        "dewpoint": "2m_dewpoint_temperature",
        "soil_moisture": "volumetric_soil_water_layer_1",
        "soil_water": "volumetric_soil_water_layer_1",
        "wind_speed": "10m_wind_speed",
        "wind": "10m_wind_speed",
        "solar_radiation": "surface_solar_radiation_downwards",
        "solar": "surface_solar_radiation_downwards",
        "evaporation": "total_evaporation",
        "runoff": "surface_runoff",
    }

    @classmethod
    def resolve_variable(cls, param_name: str) -> Tuple[str, Dict[str, Any]]:
        clean = param_name.strip().lower()
        # Direct match
        if clean in cls.VARIABLES:
            return clean, cls.VARIABLES[clean]
        # Alias match
        if clean in cls.ALIASES:
            canonical = cls.ALIASES[clean]
            return canonical, cls.VARIABLES[canonical]
        raise ProviderError(
            "era5",
            f"Unsupported variable '{param_name}'. Check /api/providers/era5/variables for supported variables."
        )

    @property
    def provider_id(self) -> str:
        return "era5"

    @property
    def name(self) -> str:
        return "Copernicus ERA5 & ERA5-Land Reanalysis"

    @property
    def description(self) -> str:
        return "ECMWF ERA5-Land (9km resolution) and ERA5 single levels reanalysis data covering 1950 to present."

    @property
    def supported_parameters(self) -> List[str]:
        # Return both canonical names and friendly aliases
        return list(self.VARIABLES.keys()) + list(self.ALIASES.keys())

    @property
    def default_resolution(self) -> str:
        return "0.1deg"  # 9km native for ERA5-Land

    @property
    def is_configured(self) -> bool:
        return settings.has_era5_credentials

    def get_extra_details(self) -> Dict[str, Any]:
        return {
            "cdsapi_url": settings.CDSAPI_URL,
            "has_credentials": self.is_configured,
            "fallback_simulation": settings.ERA5_FALLBACK_SIMULATION,
            "era5_land_resolution": "0.1deg (~9 km)",
            "era5_single_levels_resolution": "0.25deg (~31 km)",
            "total_supported_variables": len(self.VARIABLES),
            "variable_catalog_endpoint": f"{settings.API_PREFIX}/providers/era5/variables",
        }

    def get_variable_catalog(self) -> Dict[str, Any]:
        """Returns the complete structured variable catalog."""
        return {
            "total_variables": len(self.VARIABLES),
            "variables": self.VARIABLES,
            "aliases": self.ALIASES,
        }

    def fetch_data(
        self,
        request: DatasetRequest,
        latitude: float,
        longitude: float
    ) -> pd.DataFrame:
        requested_vars = request.requested_parameters
        resolved_vars: Dict[str, Dict[str, Any]] = {}
        for var_name in requested_vars:
            canonical, info = self.resolve_variable(var_name)
            resolved_vars[canonical] = info

        var_names_str = ", ".join(resolved_vars.keys())

        if self.is_configured:
            logger.info(f"CDS credentials present: Calling Copernicus CDS for [{var_names_str}] ({latitude}, {longitude})")
            try:
                return self._fetch_live_cds(request, resolved_vars, latitude, longitude)
            except Exception as e:
                logger.error(f"Live CDS call failed: {e}")
                if settings.ERA5_FALLBACK_SIMULATION:
                    logger.warning("Falling back to simulated data due to CDS error.")
                    return self._generate_simulated_data(request, resolved_vars, latitude, longitude)
                raise ProviderError(self.provider_id, f"Copernicus CDS request failed: {str(e)}")
        else:
            logger.info(f"CDSAPI_KEY not configured. Generating realistic ERA5-Land data for [{var_names_str}].")
            return self._generate_simulated_data(request, resolved_vars, latitude, longitude)

    def _fetch_live_cds(
        self,
        request: DatasetRequest,
        resolved_vars: Dict[str, Dict[str, Any]],
        latitude: float,
        longitude: float
    ) -> pd.DataFrame:
        import cdsapi
        import tempfile
        from pathlib import Path

        cds = cdsapi.Client(
            url=settings.CDSAPI_URL,
            key=settings.CDSAPI_KEY,
            quiet=True,
            progress=False,
            info_callback=_CdsStatusLog(),
            warning_callback=lambda *args: logger.warning(_cds_text(*args)),
            error_callback=lambda *args: logger.error(_cds_text(*args)),
        )

        # Collect all unique CDS variable identifiers
        cds_vars = []
        target_dataset = "reanalysis-era5-land"
        for _, info in resolved_vars.items():
            target_dataset = info.get("dataset", target_dataset)
            c_name = info["cds_name"]
            if isinstance(c_name, list):
                cds_vars.extend(c_name)
            else:
                cds_vars.append(c_name)
        cds_vars = list(dict.fromkeys(cds_vars))

        start = datetime.datetime.strptime(request.start_date, "%Y-%m-%d")
        end = datetime.datetime.strptime(request.end_date, "%Y-%m-%d")
        # Ask CDS only for the selected dates. A full year/month/day grid
        # makes a one-week request retrieve a whole year and wait much longer.
        selected_dates = pd.date_range(start=start, end=end, freq="D")
        years = sorted({d.strftime("%Y") for d in selected_dates})
        months = sorted({d.strftime("%m") for d in selected_dates})
        days = sorted({d.strftime("%d") for d in selected_dates})

        if request.hours:
            times = request.hours
        elif (request.temporal_resolution or "daily").lower() == "daily":
            times = ["00:00"]
        else:
            times = [f"{hour:02d}:00" for hour in range(24)]

        delta = 0.05  # tight box around the point; 0.1 pulls extra grid cells
        area = [
            round(latitude + delta, 2),
            round(longitude - delta, 2),
            round(latitude - delta, 2),
            round(longitude + delta, 2),
        ]

        query = {
            "format": "netcdf",
            "variable": cds_vars,
            "year": years,
            "month": months,
            "day": days,
            "time": times,
            "area": area,
        }

        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            cds.retrieve(target_dataset, query, tmp_path)
            import xarray as xr
            # Load fully into memory before the temp file is removed. On Windows,
            # xarray keeps the NetCDF file locked until the dataset is closed.
            with xr.open_dataset(tmp_path) as ds:
                loaded = ds.load()
                df = loaded.to_dataframe().reset_index()
                data_var_names = list(loaded.data_vars)

            # Identify timestamp column
            time_col = None
            for c in ["valid_time", "time", "date"]:
                if c in df.columns:
                    time_col = c
                    break
            if time_col:
                df["timestamp"] = pd.to_datetime(df[time_col]).dt.strftime("%Y-%m-%d %H:%M:%S")
            else:
                df["timestamp"] = pd.date_range(start=start, periods=len(df), freq="h").strftime("%Y-%m-%d %H:%M:%S")

            df["latitude"] = latitude
            df["longitude"] = longitude

            # Map extracted variables
            final_cols = ["timestamp", "latitude", "longitude"]
            for canonical_var, info in resolved_vars.items():
                cds_name = info["cds_name"]
                found_col = None
                candidates = [cds_name] if isinstance(cds_name, str) else cds_name
                for cand in candidates:
                    if cand in df.columns:
                        found_col = cand
                        break
                if not found_col:
                    for dv in data_var_names:
                        if dv in df.columns and dv not in final_cols:
                            found_col = dv
                            break

                if found_col:
                    vals = df[found_col].astype(float)
                    if info["unit"] == "degC" and (vals > 100.0).any():
                        vals = vals - 273.15
                    elif info["unit"] == "mm" and (vals < 1.0).all():
                        vals = vals * 1000.0
                    df[canonical_var] = vals.round(3)
                else:
                    df[canonical_var] = 0.0
                final_cols.append(canonical_var)

            # For single-variable backward compatibility, also include 'value', 'unit', 'parameter'
            if len(resolved_vars) == 1:
                first_var = list(resolved_vars.keys())[0]
                df["value"] = df[first_var]
                df["unit"] = resolved_vars[first_var]["unit"]
                df["parameter"] = first_var
                final_cols.extend(["value", "unit", "parameter"])

            return df[final_cols]
        finally:
            p = Path(tmp_path)
            if p.exists():
                try:
                    p.unlink()
                except OSError as unlink_err:
                    logger.warning(f"Could not remove CDS temp file {tmp_path}: {unlink_err}")

    def _generate_simulated_data(
        self,
        request: DatasetRequest,
        resolved_vars: Dict[str, Dict[str, Any]],
        latitude: float,
        longitude: float
    ) -> pd.DataFrame:
        start = datetime.datetime.strptime(request.start_date, "%Y-%m-%d")
        end = datetime.datetime.strptime(request.end_date, "%Y-%m-%d")

        if end < start:
            raise ProviderError(self.provider_id, "end_date must be on or after start_date")

        date_range = pd.date_range(start=start, end=end, freq="D")
        is_northern = latitude >= 0
        is_tropical = abs(latitude) < 25

        base_df = pd.DataFrame({
            "timestamp": date_range.strftime("%Y-%m-%d"),
            "latitude": latitude,
            "longitude": longitude,
        })

        for canonical_var, var_info in resolved_vars.items():
            seed_str = f"era5_{canonical_var}_{request.location}_{latitude:.4f}_{longitude:.4f}_{request.start_date}_{request.end_date}"
            seed = int(hashlib.md5(seed_str.encode()).hexdigest()[:8], 16)
            rng = np.random.default_rng(seed)

            category = var_info["category"]
            vals = []

            for dt in date_range:
                day_of_year = dt.timetuple().tm_yday
                month = dt.month
                seasonal_phase = math.cos(2 * math.pi * (day_of_year - (200 if is_northern else 20)) / 365.25)

                if category == "temperature":
                    base_temp = 27.0 if is_tropical else 16.0
                    amp = 7.0 if is_tropical else 12.0
                    val = base_temp - (amp * seasonal_phase) + rng.normal(0, 1.8)
                    if canonical_var == "2m_dewpoint_temperature":
                        val -= rng.uniform(2.0, 7.0)
                    elif canonical_var == "skin_temperature":
                        val += rng.uniform(-1.0, 2.5)
                    vals.append(round(val, 2))

                elif category == "hydrology":
                    if canonical_var == "total_precipitation":
                        is_wet = 6 <= month <= 9 if is_tropical else 4 <= month <= 10
                        prob = 0.52 if is_wet else 0.08
                        val = round(float(rng.gamma(1.1 if is_wet else 0.6, 16.0 if is_wet else 4.0)), 2) if rng.random() < prob else 0.0
                    elif canonical_var in ["total_evaporation", "potential_evaporation"]:
                        base_evap = 4.5 - (1.5 * seasonal_phase)
                        val = round(max(0.2, base_evap + rng.normal(0, 0.4)), 2)
                    elif "runoff" in canonical_var:
                        val = round(max(0.0, float(rng.exponential(0.8))), 2)
                    else:
                        val = round(float(rng.uniform(0.0, 10.0)), 2)
                    vals.append(val)

                elif category == "soil_moisture":
                    layer_depth_factor = {
                        "volumetric_soil_water_layer_1": 0.04,
                        "volumetric_soil_water_layer_2": 0.03,
                        "volumetric_soil_water_layer_3": 0.02,
                        "volumetric_soil_water_layer_4": 0.01,
                    }.get(canonical_var, 0.03)
                    base_sm = 0.28 if (6 <= month <= 9) else 0.16
                    val = base_sm + rng.normal(0, layer_depth_factor)
                    vals.append(round(max(0.05, min(0.48, val)), 3))

                elif category == "soil_temperature":
                    base_st = 26.0 if is_tropical else 15.0
                    vals.append(round(base_st - (8.0 * seasonal_phase) + rng.normal(0, 1.0), 2))

                elif category == "radiation":
                    base_rad = 20.0 - (7.0 * seasonal_phase)
                    vals.append(round(max(4.0, base_rad + rng.normal(0, 2.0)), 2))

                elif category == "wind":
                    if canonical_var == "10m_wind_speed":
                        vals.append(round(float(rng.weibull(2.0) * 4.2), 2))
                    else:
                        vals.append(round(float(rng.normal(1.5, 2.0)), 2))

                else:
                    vals.append(round(float(rng.uniform(10.0, 50.0)), 2))

            base_df[canonical_var] = vals

        # If single variable, maintain backward compatible 'value', 'unit', 'parameter' columns
        if len(resolved_vars) == 1:
            first_var = list(resolved_vars.keys())[0]
            base_df["value"] = base_df[first_var]
            base_df["unit"] = resolved_vars[first_var]["unit"]
            base_df["parameter"] = first_var

        return base_df
