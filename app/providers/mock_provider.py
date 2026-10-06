from typing import List, Dict, Any
import datetime
import math
import hashlib
import pandas as pd
import numpy as np

from app.schemas.dataset import DatasetRequest
from app.providers.base import BaseProvider


class FrontendMockProvider(BaseProvider):
    """
    Mock Provider to support frontend test options (provider-a, provider-b, provider-c, provider-d).
    """

    def __init__(self, provider_id: str, default_param: str, name: str):
        self._id = provider_id
        self._param = default_param
        self._name = name

    @property
    def provider_id(self) -> str:
        return self._id

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"Simulated weather provider ({self._name}) for {self._param} parametric contracts."

    @property
    def supported_parameters(self) -> List[str]:
        return [self._param, "rainfall", "temperature", "wind_speed"]

    @property
    def default_resolution(self) -> str:
        return "1km"

    @property
    def is_configured(self) -> bool:
        return True

    def fetch_data(
        self,
        request: DatasetRequest,
        latitude: float,
        longitude: float
    ) -> pd.DataFrame:
        start = datetime.datetime.strptime(request.start_date, "%Y-%m-%d")
        end = datetime.datetime.strptime(request.end_date, "%Y-%m-%d")

        seed_str = f"{self._id}_{request.location}_{request.parameter_type}_{request.start_date}_{request.end_date}"
        seed = int(hashlib.md5(seed_str.encode()).hexdigest()[:8], 16)
        rng = np.random.default_rng(seed)

        date_range = pd.date_range(start=start, end=end, freq="D")
        param = request.parameter_type.lower()

        unit = "mm" if param in ["rainfall", "precipitation"] else "degC" if param == "temperature" else "units"
        records = []

        for dt in date_range:
            month = dt.month
            if param in ["rainfall", "precipitation"]:
                is_wet = 6 <= month <= 9
                prob = 0.5 if is_wet else 0.12
                val = round(float(rng.gamma(1.1, 15.0)), 2) if rng.random() < prob else 0.0
            elif param == "temperature":
                val = round(float(25.0 + rng.normal(0, 3.5)), 2)
            else:
                val = round(float(rng.uniform(5.0, 45.0)), 2)

            records.append({
                "timestamp": dt.strftime("%Y-%m-%d"),
                "value": val,
                "unit": unit,
                "parameter": param,
                "latitude": latitude,
                "longitude": longitude,
            })

        return pd.DataFrame(records)
