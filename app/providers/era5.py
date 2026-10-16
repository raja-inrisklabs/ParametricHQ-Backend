import datetime
import math
import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import pandas as pd
import numpy as np

from app.core.config import settings
from app.core.logging import logger
from app.core.exceptions import ProviderError
from app.schemas.dataset import DatasetRequest
from app.providers.base import BaseProvider
from app.providers.datasets import get_dataset


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
            f"Unsupported variable '{param_name}'. Check {settings.API_PREFIX}/providers/era5/variables for supported variables."
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
        if request.dataset_id:
            return self._fetch_catalog_day(request, latitude, longitude)

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

    def _fetch_catalog_day(
        self,
        request: DatasetRequest,
        latitude: float,
        longitude: float,
    ) -> pd.DataFrame:
        """One calendar day of hourly ERA5, every catalog variable, long format."""
        catalog = get_dataset(request.dataset_id or "")
        day = datetime.datetime.strptime(request.start_date, "%Y-%m-%d")
        available = list(catalog.get("hourly_variables") or catalog["variables"])
        selected = [name for name in (request.parameters or []) if name in available]
        variables = selected or available
        if request.fetch_variables:
            variables = [name for name in variables if name in request.fetch_variables]
        units = catalog["units"]

        if self.is_configured:
            logger.info(
                f"CDS credentials present: {catalog['cds_dataset']} {request.start_date} "
                f"({len(variables)} variables, 24 hours)"
            )
            try:
                end = datetime.datetime.strptime(request.end_date, "%Y-%m-%d")
                wide = self._fetch_live_catalog_day(catalog, day, end, request.cds_area(), variables)
            except Exception as exc:
                logger.error(f"Live CDS call failed: {exc}")
                raise ProviderError(self.provider_id, f"Copernicus CDS request failed: {exc}") from exc
        else:
            logger.info(
                f"CDSAPI_KEY not configured. Simulating the full grid for {catalog['id']} {request.start_date}."
            )
            end = datetime.datetime.strptime(request.end_date, "%Y-%m-%d")
            wide = self._simulate_catalog_grid(
                day,
                variables,
                units,
                request.cds_area(),
                request.location,
                end,
                step=float(str(catalog.get("resolution", "0.1deg")).replace("deg", "")),
            )

        return self._to_long_format(wide, variables, units, latitude, longitude, catalog.get("name") or catalog["id"])

    def _fetch_live_catalog_day(
        self,
        catalog: Dict[str, Any],
        start: datetime.datetime,
        end: datetime.datetime,
        area: List[float],
        request_variables: Optional[List[str]] = None,
    ) -> pd.DataFrame:
        import cdsapi
        import tempfile


        cds = cdsapi.Client(
            url=settings.CDSAPI_URL,
            key=settings.CDSAPI_KEY,
            quiet=True,
            progress=False,
            info_callback=_CdsStatusLog(),
            warning_callback=lambda *args: logger.warning(_cds_text(*args)),
            error_callback=lambda *args: logger.error(_cds_text(*args)),
        )
        # Same shape as the official sample: product_type, variable, one day, all hours, area.
        # download_format zip matches the sample. The retrieve target is the zip path.
        dates = [start + datetime.timedelta(days=offset) for offset in range((end - start).days + 1)]
        logger.info(f"CDS area [north, west, south, east]={area} days={len(dates)}")
        # Keep the dataset's native grid. Do not add an interpolation grid.
        hourly = [
            name for name in (catalog.get("hourly_variables") or catalog["variables"])
            if not request_variables or name in request_variables
        ]
        # Fixed land fields are only added when the user asked for the full catalog.
        invariant = [] if request_variables else (catalog.get("invariant_variables") or [])
        # One call per year. CDS combines every year, month, and day, so a
        # 2023-2024 request in one call would also return months outside the range.
        chunks = self._year_chunks(dates)
        with tempfile.TemporaryDirectory() as tmp_dir:
            frames = []
            for index, chunk in enumerate(chunks, start=1):
                logger.info(f"CDS request {index}/{len(chunks)}: {chunk[0].date()} to {chunk[-1].date()}")
                query = {
                    "product_type": [catalog["product_type"]],
                    "variable": hourly,
                    "year": sorted({item.strftime("%Y") for item in chunk}),
                    "month": sorted({item.strftime("%m") for item in chunk}),
                    "day": sorted({item.strftime("%d") for item in chunk}),
                    "time": catalog["times"],
                    "data_format": "netcdf",
                    "download_format": "zip",
                    "area": area,
                }
                frames.append(
                    self._retrieve_catalog_frame(cds, catalog, query, Path(tmp_dir) / f"month-{index}", hourly)
                )
            if invariant:
                fixed_query = {
                    "product_type": [catalog["product_type"]],
                    "variable": invariant,
                    "year": [start.strftime("%Y")],
                    "month": [start.strftime("%m")],
                    "day": [start.strftime("%d")],
                    "time": ["00:00"],
                    "data_format": "netcdf",
                    "download_format": "zip",
                    "area": area,
                }
                frames.append(self._retrieve_catalog_frame(cds, catalog, fixed_query, Path(tmp_dir) / "fixed", invariant))
            wide = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]
            stamps = pd.to_datetime(wide["timestamp"])
            keep = (stamps >= pd.Timestamp(start)) & (stamps < pd.Timestamp(end) + pd.Timedelta(days=1))
            return wide.loc[keep].reset_index(drop=True)

    @staticmethod
    def _year_chunks(dates: List[datetime.datetime]) -> List[List[datetime.datetime]]:
        """Group a range into one CDS call per calendar year.

        CDS treats year, month, and day as independent lists and returns every
        combination. A range inside one year can be one call. A range that
        crosses a year needs one call per year, otherwise the shared month and
        day lists pull months the user did not ask for.
        """
        chunks: List[List[datetime.datetime]] = []
        current: List[datetime.datetime] = []
        current_year = None
        for item in dates:
            if current and item.year != current_year:
                chunks.append(current)
                current = []
            current.append(item)
            current_year = item.year
        if current:
            chunks.append(current)
        return chunks

    def _retrieve_catalog_frame(
        self,
        cds,
        catalog: Dict[str, Any],
        query: Dict[str, Any],
        folder: Path,
        variables: List[str],
    ) -> pd.DataFrame:
        folder.mkdir(parents=True, exist_ok=True)
        zip_path = folder / "cds.zip"
        cds.retrieve(catalog["cds_dataset"], query, str(zip_path))
        nc_files = self._unpack_cds_download(zip_path, folder)
        return self._netcdf_files_to_wide(nc_files, variables, catalog)

    @staticmethod
    def _merge_wide_frames(frames: List[pd.DataFrame]) -> pd.DataFrame:
        frames = [frame for frame in frames if not frame.empty]
        if not frames:
            raise ProviderError("era5", "CDS returned no ERA5-Land variables.")
        merged = frames[0]
        for extra in frames[1:]:
            shared = [name for name in ("timestamp", "latitude", "longitude") if name in merged.columns and name in extra.columns]
            new_cols = [name for name in extra.columns if name not in merged.columns]
            if not new_cols:
                continue
            if "timestamp" in extra.columns and "timestamp" in shared:
                merged = merged.merge(extra[shared + new_cols], on=shared, how="left")
            else:
                point_cols = [name for name in ("latitude", "longitude") if name in shared]
                merged = merged.merge(extra[point_cols + new_cols], on=point_cols, how="left")
        return merged

    @staticmethod
    def _is_point_area(area: List[float]) -> bool:
        north, west, south, east = area
        return (north - south) <= 0.11 and (east - west) <= 0.11

    @staticmethod
    def _unpack_cds_download(download: Path, folder: Path) -> List[Path]:
        import zipfile

        if zipfile.is_zipfile(download):
            with zipfile.ZipFile(download) as archive:
                archive.extractall(folder)
        matches = sorted(folder.rglob("*.nc"))
        if not matches and download.suffix == ".nc":
            return [download]
        if not matches:
            raise ProviderError("era5", f"CDS download had no NetCDF file: {download.name}")
        return matches

    def _netcdf_files_to_wide(self, paths: List[Path], variables: List[str], catalog: Optional[Dict[str, Any]] = None) -> pd.DataFrame:
        import xarray as xr

        # Load each file fully, then close it. Windows locks a NetCDF until xarray closes it.
        frames = []
        for path in paths:
            with xr.open_dataset(path) as ds:
                loaded = self._rename_dataset_variables(ds, variables, catalog).load()
            frames.append(loaded.to_dataframe().reset_index())
            loaded.close()
        df = frames[0]
        for extra in frames[1:]:
            shared = [
                name for name in ("valid_time", "time", "latitude", "longitude")
                if name in df.columns and name in extra.columns
            ]
            # Wave fields are on a different time coordinate than atmospheric fields.
            if "valid_time" not in shared and {"latitude", "longitude"} <= set(shared):
                extra = extra.drop(columns=[name for name in ("valid_time", "time") if name in extra.columns])
                shared = ["latitude", "longitude"]
            new_cols = [name for name in extra.columns if name not in df.columns]
            if shared and new_cols:
                df = df.merge(extra[shared + new_cols], on=shared, how="outer")
            else:
                df = pd.concat([df, extra], ignore_index=True)
        return self._wide_frame(df, variables, catalog)

    def _netcdf_to_wide(self, tmp_path: str, variables: List[str]) -> pd.DataFrame:
        return self._netcdf_files_to_wide([Path(tmp_path)], variables)

    def _wide_frame(self, df: pd.DataFrame, variables: List[str], catalog: Optional[Dict[str, Any]] = None) -> pd.DataFrame:

        time_col = next((name for name in ("valid_time", "time", "date") if name in df.columns), None)
        if time_col:
            df["timestamp"] = pd.to_datetime(df[time_col]).dt.strftime("%Y-%m-%d %H:%M:%S")
        lat_col = next((name for name in ("latitude", "lat") if name in df.columns), None)
        lon_col = next((name for name in ("longitude", "lon") if name in df.columns), None)

        keep = ["timestamp"]
        if lat_col:
            df["latitude"] = df[lat_col].astype(float)
            keep.append("latitude")
        if lon_col:
            df["longitude"] = df[lon_col].astype(float)
            keep.append("longitude")

        rename = self._column_rename(df.columns, variables, catalog)
        if rename:
            df = df.rename(columns=rename)

        missing = [name for name in variables if name not in df.columns]
        if missing:
            logger.warning(
                "CDS file is missing %s of %s catalog variables: %s",
                len(missing),
                len(variables),
                ", ".join(missing),
            )
        if len(missing) == len(variables):
            found = ", ".join(str(column) for column in list(df.columns)[:20])
            raise ProviderError("era5", f"CDS file had none of the catalog variables. Columns: {found}")

        for name in variables:
            values = pd.to_numeric(df[name], errors="coerce")
            unit = ((catalog or {}).get("units") or {}).get(name, "")
            if unit == "degC" and (values > 100.0).any():
                values = values - 273.15
            df[name] = values
            keep.append(name)
        return df[keep]

    @staticmethod
    def _rename_dataset_variables(dataset, variables: List[str], catalog: Optional[Dict[str, Any]] = None):
        wanted = set(variables)
        short_to_name = {
            short: name
            for name, shorts in (catalog or {}).get("short_names", {}).items()
            for short in shorts
        }
        rename = {}
        for column, values in dataset.data_vars.items():
            if column in wanted:
                continue
            short = values.attrs.get("GRIB_shortName") or values.attrs.get("short_name") or column
            if short in short_to_name and short_to_name[short] in wanted:
                rename[column] = short_to_name[short]
                continue
            long_name = str(values.attrs.get("long_name") or "").strip().lower().replace(" ", "_")
            if long_name in wanted:
                rename[column] = long_name
        return dataset.rename(rename) if rename else dataset

    @staticmethod
    def _column_rename(columns, variables: List[str], catalog: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
        present = set(columns)
        wanted = set(variables)
        rename = {}
        for name, shorts in (catalog or {}).get("short_names", {}).items():
            if name in present or name not in wanted:
                continue
            for short in shorts:
                if short in present and short not in rename:
                    rename[short] = name
                    break
        return rename

    def _simulate_catalog_grid(
        self,
        day: datetime.datetime,
        variables: List[str],
        units: Dict[str, str],
        area: List[float],
        location: str,
        end: Optional[datetime.datetime] = None,
        step: float = 0.1,
    ) -> pd.DataFrame:
        """One point per native grid cell inside the box."""
        north, west, south, east = area
        # Tests use a coarse step so a box does not write millions of random rows.
        if settings.ENVIRONMENT == "testing":
            step = 1.0
        latitudes = np.round(np.round(np.arange(south, north + step / 10, step) / step) * step, 2)
        longitudes = np.round(np.round(np.arange(west, east + step / 10, step) / step) * step, 2)
        stop = end or day
        hours = pd.date_range(start=day, end=stop + datetime.timedelta(hours=23), freq="h")
        grid = pd.MultiIndex.from_product(
            [hours, latitudes, longitudes],
            names=["timestamp", "latitude", "longitude"],
        ).to_frame(index=False)
        grid["timestamp"] = pd.to_datetime(grid["timestamp"]).dt.strftime("%Y-%m-%d %H:%M:%S")
        for name in variables:
            seed_str = f"era5_grid_{name}_{location}_{day.date().isoformat()}_{north}_{west}_{south}_{east}"
            seed = int(hashlib.md5(seed_str.encode()).hexdigest()[:8], 16)
            rng = np.random.default_rng(seed)
            unit = units.get(name, "")
            size = len(grid)
            if unit == "degC":
                values = 22.0 + rng.normal(0, 1.5, size)
            elif unit == "m/s":
                values = rng.normal(1.5, 1.2, size)
            elif unit == "m":
                values = np.clip(rng.exponential(0.0004, size), 0, None)
            elif unit in ("0-1", "1"):
                values = np.clip(rng.uniform(0.05, 0.8, size), 0, 1)
            else:
                values = np.abs(rng.normal(10.0, 3.0, size))
            grid[name] = np.round(values.astype(float), 5)
        return grid

    def _simulate_catalog_day(
        self,
        day: datetime.datetime,
        variables: List[str],
        units: Dict[str, str],
        latitude: float,
        longitude: float,
        location: str,
    ) -> pd.DataFrame:
        hours = pd.date_range(start=day, periods=24, freq="h")
        frame = pd.DataFrame({
            "timestamp": hours.strftime("%Y-%m-%d %H:%M:%S"),
            "latitude": latitude,
            "longitude": longitude,
        })
        for name in variables:
            seed_str = f"era5_hourly_{name}_{location}_{day.date().isoformat()}"
            seed = int(hashlib.md5(seed_str.encode()).hexdigest()[:8], 16)
            rng = np.random.default_rng(seed)
            unit = units.get(name, "")
            if unit == "degC":
                values = 22.0 + 6.0 * np.sin((hours.hour - 8) / 24 * 2 * math.pi) + rng.normal(0, 0.4, 24)
            elif unit == "m/s":
                values = rng.normal(1.5, 1.2, 24)
            elif unit == "m":
                values = np.clip(rng.exponential(0.0004, 24), 0, None)
            elif unit in ("0-1", "1"):
                values = np.clip(rng.uniform(0.05, 0.8, 24), 0, 1)
            elif unit == "kg/m3":
                values = rng.normal(1.2, 0.02, 24)
            elif unit == "radians":
                values = rng.uniform(0, math.pi, 24)
            else:
                values = np.abs(rng.normal(10.0, 3.0, 24))
            frame[name] = np.round(values.astype(float), 5)
        return frame

    @staticmethod
    def _to_long_format(
        wide: pd.DataFrame,
        variables: List[str],
        units: Dict[str, str],
        latitude: float,
        longitude: float,
        dataset_name: str = "",
    ) -> pd.DataFrame:
        present = [name for name in variables if name in wide.columns]
        if not present:
            raise ProviderError("era5", "CDS file contained none of the catalog variables.")
        id_cols = [name for name in ("timestamp", "latitude", "longitude") if name in wide.columns]
        long = wide.melt(
            id_vars=id_cols,
            value_vars=present,
            var_name="variable",
            value_name="value",
        )
        if "latitude" not in long.columns:
            long["latitude"] = latitude
        if "longitude" not in long.columns:
            long["longitude"] = longitude
        long["unit"] = long["variable"].map(units)
        long["value"] = pd.to_numeric(long["value"], errors="coerce")
        long["dataset"] = dataset_name
        return long[["timestamp", "latitude", "longitude", "dataset", "variable", "value", "unit"]].reset_index(drop=True)
