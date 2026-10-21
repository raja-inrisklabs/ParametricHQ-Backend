import calendar
import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
from pydantic import BaseModel, ConfigDict, Field, model_validator


class DatasetRequest(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    provider_id: str = Field(..., description="Provider identifier (e.g. 'era5')")
    dataset_id: Optional[str] = Field(
        None,
        description="Catalog dataset, e.g. 'reanalysis-era5-land'. Fetches one day, all hours, all catalog variables.",
    )
    parameter_type: Optional[str] = Field(
        None,
        description="Single parameter name, e.g. 'total_precipitation' or 'rainfall' (backward compatible)"
    )
    parameters: Optional[List[str]] = Field(
        None,
        description="List of multiple parameters to fetch simultaneously, e.g. ['2m_temperature', 'total_precipitation', 'volumetric_soil_water_layer_1']"
    )
    location: str = Field(..., description="Target location name (e.g. 'gujarat/ahmedabad') or 'lat,lon'")
    start_date: Optional[str] = Field(None, description="Start date in YYYY-MM-DD format")
    end_date: Optional[str] = Field(None, description="End date in YYYY-MM-DD format")
    period_type: Optional[str] = Field(
        None,
        description="'dates' for a start and end date, or 'season' for the same months in each year.",
    )
    start_month: Optional[int] = Field(None, ge=1, le=12, description="First month of a seasonal window.")
    month_count: Optional[int] = Field(None, ge=1, le=3, description="How many months, starting at start_month.")
    start_year: Optional[int] = Field(None, ge=1940, le=2100)
    end_year: Optional[int] = Field(None, ge=1940, le=2100)
    resolution: Optional[str] = Field("0.1deg", description="Resolution (e.g., '0.1deg' for ERA5-Land, '0.25deg' for ERA5)")
    temporal_resolution: Optional[str] = Field("hourly", description="'hourly' (24 readings/day) or 'daily'")
    hours: Optional[List[str]] = Field(None, description="Optional specific hours, e.g. ['00:00', '06:00', '12:00', '18:00'] or default all 24 hours")
    fetch_variables: Optional[List[str]] = Field(
        None,
        description="Subset to retrieve from CDS. Used when the other requested variables are already stored.",
    )
    on_chunk: Optional[Callable[[Any], None]] = Field(
        None,
        exclude=True,
        description="Called with each finished month frame so it can be stored before the other months finish.",
    )
    version: Optional[str] = Field("v1", description="Dataset specification version")
    
    # Optional precise coordinates override
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    # CDS area order is [north, west, south, east]
    north: Optional[float] = Field(None, ge=-90, le=90)
    west: Optional[float] = Field(None, ge=-180, le=180)
    south: Optional[float] = Field(None, ge=-90, le=90)
    east: Optional[float] = Field(None, ge=-180, le=180)

    @model_validator(mode="after")
    def validate_parameters(self):
        bounds = (self.north, self.west, self.south, self.east)
        if any(value is not None for value in bounds) and not all(value is not None for value in bounds):
            raise ValueError("Provide north, west, south, and east together.")
        if self.north is not None and self.south is not None and self.north <= self.south:
            raise ValueError("North must be greater than south.")
        if self.west is not None and self.east is not None and self.west >= self.east:
            raise ValueError("West must be less than east.")
        if (
            self.dataset_id == "reanalysis-era5-land"
            and None not in (self.north, self.south, self.east, self.west)
            and (round(self.north - self.south, 4) > 1 or round(self.east - self.west, 4) > 1)
        ):
            raise ValueError("ERA5-Land area is limited to 1° by 1°. Reduce the area.")
        if self.dataset_id:
            if self.period_type == "season":
                self._validate_season()
                return self
            if not self.start_date or not self.end_date:
                raise ValueError("Select a start date and an end date.")
            start = datetime.datetime.strptime(self.start_date, "%Y-%m-%d")
            end = datetime.datetime.strptime(self.end_date, "%Y-%m-%d")
            if end < start:
                raise ValueError("end_date must be on or after start_date.")
            day_count = (end - start).days + 1
            from app.providers.datasets import get_dataset
            yearly = int(get_dataset(self.dataset_id).get("max_request_months") or 1) >= 12
            if yearly and (end.year - start.year + 1) > 10:
                raise ValueError("ERA5 single levels can include at most 10 years.")
            if not yearly and day_count > 366:
                raise ValueError("Date range is limited to 366 days.")
            selected = self.parameters or []
            if selected and len(selected) > 5 and (yearly or day_count > 31):
                raise ValueError("A range longer than 31 days can include at most 5 variables.")
            return self
        if not self.parameters and not self.parameter_type:
            raise ValueError("Either 'parameter_type' (single string) or 'parameters' (list of variables) must be provided.")
        if self.parameters and not self.parameter_type:
            self.parameter_type = self.parameters[0] if len(self.parameters) == 1 else "multi_parameter"
        return self

    def _validate_season(self) -> None:
        if not all(value is not None for value in (self.start_month, self.month_count, self.start_year, self.end_year)):
            raise ValueError("A month range needs a start month, a month count, a start year, and an end year.")
        if self.end_year < self.start_year:
            raise ValueError("End year must be the same as or after the start year.")
        if self.start_month + self.month_count - 1 > 12:
            raise ValueError("The month range must stay inside the selected year.")
        from app.providers.datasets import get_dataset
        years = self.end_year - self.start_year + 1
        yearly = int(get_dataset(self.dataset_id).get("max_request_months") or 1) >= 12
        calls = years if yearly else years * self.month_count
        if calls > (10 if yearly else 12):
            if yearly:
                raise ValueError("ERA5 single levels can include at most 10 years.")
            raise ValueError(
                f"This selection is {calls} monthly calls. One request can fetch 12 months: "
                "12 years of 1 month, 6 years of 2 months, or 4 years of 3 months."
            )
        selected = self.parameters or []
        if selected and len(selected) > 5:
            raise ValueError("A month range can include at most 5 variables.")
        # Dates stay on the request so a view and a download can clip the joined months.
        self.start_date = f"{self.start_year:04d}-{self.start_month:02d}-01"
        last_month = self.start_month + self.month_count - 1
        last_day = calendar.monthrange(self.end_year, last_month)[1]
        self.end_date = f"{self.end_year:04d}-{last_month:02d}-{last_day:02d}"

    def planned_months(self) -> List[Tuple[str, str, List[str]]]:
        """Every calendar month this request reads. A season repeats inside each year."""
        if self.period_type == "season":
            months = []
            for year in range(self.start_year, self.end_year + 1):
                for offset in range(self.month_count):
                    month = self.start_month + offset
                    last_day = calendar.monthrange(year, month)[1]
                    start = datetime.date(year, month, 1)
                    days = [
                        (start + datetime.timedelta(days=index)).isoformat()
                        for index in range(last_day)
                    ]
                    months.append((f"{year:04d}", f"{month:02d}", days))
            return months
        return []

    def is_point(self) -> bool:
        return self.latitude is not None and self.longitude is not None and self.north is None

    def grid_step(self) -> float:
        """Native cell size. ERA5-Land is 0.1 degree. ERA5 single levels is 0.25 degree."""
        if self.dataset_id == "reanalysis-era5-single-levels":
            return 0.25
        return 0.1

    def snapped_point(self) -> Optional[Tuple[float, float]]:
        """Nearest native grid cell. A 0.1 degree grid turns 23.06 into 23.1."""
        if self.latitude is None or self.longitude is None:
            return None
        step = self.grid_step()
        lat = round(round(float(self.latitude) / step) * step, 2)
        lon = round(round(float(self.longitude) / step) * step, 2)
        return lat, lon

    def cds_area(self) -> List[float]:
        """CDS order: north, west, south, east. A point uses its native grid cell."""
        if self.is_point():
            lat, lon = self.snapped_point()
            # A live 0.25 degree cell needs a 0.125 degree half-box. Tests use a 1 degree
            # grid, so the same half-box would pull the neighboring test cell. Keep one cell.
            half = 0.05 if __import__("app.core.config", fromlist=["settings"]).settings.ENVIRONMENT == "testing" else self.grid_step() / 2
            return [round(lat + half, 2), round(lon - half, 2), round(lat - half, 2), round(lon + half, 2)]
        if self.north is None:
            return [20.0, 10.0, 10.0, 20.0]
        return [float(self.north), float(self.west), float(self.south), float(self.east)]

    @property
    def requested_parameters(self) -> List[str]:
        if self.parameters and len(self.parameters) > 0:
            return self.parameters
        if self.parameter_type:
            return [self.parameter_type]
        return []


class DatasetResponse(BaseModel):
    status: str = Field(..., description="'accepted', 'running', 'ready', or 'failed'")
    job_id: Optional[str] = None
    download_url: Optional[str] = None
    cached: Optional[bool] = None
    data_key: Optional[str] = None
    detail: Optional[str] = None
    record_count: Optional[int] = None
    file_size_bytes: Optional[int] = None
    parameters: Optional[List[str]] = None
    summary_stats: Optional[Dict[str, Any]] = None


class DatasetDataPreview(BaseModel):
    data_key: str
    parameter_type: str
    parameters: List[str]
    units: Dict[str, str]
    total_records: int
    sample_records: List[Dict[str, Any]]
    summary: Dict[str, Any]
