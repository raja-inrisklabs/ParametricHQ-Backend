from typing import Dict, Any, List
import pandas as pd
import numpy as np

from app.core.exceptions import PlatformException
from app.services.storage_service import storage_backend


class DatasetAnalysisService:
    """
    Performs comprehensive statistical, temporal, and distribution analysis
    on single and multi-variable environmental and climate datasets.
    """

    @classmethod
    def analyze_dataset(cls, data_key: str) -> Dict[str, Any]:
        if not storage_backend.dataset_exists(data_key):
            raise PlatformException(f"Dataset '{data_key}' was not found in storage.", status_code=404)

        df = storage_backend.read_dataset(data_key)
        if df.empty:
            raise PlatformException(f"Dataset '{data_key}' is empty.", status_code=400)

        meta = storage_backend.get_metadata(data_key) or {}
        
        # Ensure timestamp is datetime
        df["dt"] = pd.to_datetime(df["timestamp"])
        df = df.sort_values("dt").reset_index(drop=True)

        start_date = df["dt"].min()
        end_date = df["dt"].max()
        total_calendar_days = (end_date.normalize() - start_date.normalize()).days + 1
        # Hourly catalog files store one row per variable per hour. Completeness
        # is measured on unique timestamps, not raw row count.
        unique_times = df["dt"].nunique()
        expected_slots = total_calendar_days * 24 if unique_times > total_calendar_days else total_calendar_days
        missing_days = max(0, expected_slots - unique_times)
        completeness_pct = round((unique_times / expected_slots) * 100.0, 2) if expected_slots > 0 else 100.0

        temporal = {
            "start_date": str(start_date.date()),
            "end_date": str(end_date.date()),
            "total_records": len(df),
            "calendar_span_days": total_calendar_days,
            "missing_records": missing_days,
            "completeness_percentage": completeness_pct,
        }

        # Identify all analytical variable columns
        var_cols = [
            c for c in df.columns 
            if c not in ["timestamp", "dt", "latitude", "longitude", "value", "unit", "parameter"]
        ]
        if not var_cols and "value" in df.columns:
            var_cols = ["value"]

        # 1. Compute per-variable descriptive statistics and distribution
        variable_stats = {}
        for col in var_cols:
            vals = df[col].dropna().to_numpy()
            if len(vals) == 0:
                continue

            hist, bin_edges = np.histogram(vals, bins=10)
            bins = [
                {
                    "bin_start": round(float(bin_edges[i]), 2),
                    "bin_end": round(float(bin_edges[i+1]), 2),
                    "count": int(hist[i]),
                    "percentage": round(float(hist[i] / len(vals) * 100.0), 2),
                }
                for i in range(len(hist))
            ]

            top_highest = (
                df.nlargest(5, col)[["timestamp", col]]
                .rename(columns={col: "value"})
                .to_dict(orient="records")
            )
            top_lowest = (
                df.nsmallest(5, col)[["timestamp", col]]
                .rename(columns={col: "value"})
                .to_dict(orient="records")
            )

            zero_count = int(np.sum(vals == 0.0))
            zero_pct = round((zero_count / len(vals)) * 100.0, 2)

            variable_stats[col] = {
                "count": int(len(vals)),
                "sum": round(float(np.sum(vals)), 3),
                "mean": round(float(np.mean(vals)), 3),
                "std_dev": round(float(np.std(vals, ddof=1)), 3) if len(vals) > 1 else 0.0,
                "min": round(float(np.min(vals)), 3),
                "p25": round(float(np.percentile(vals, 25)), 3),
                "median": round(float(np.median(vals)), 3),
                "p75": round(float(np.percentile(vals, 75)), 3),
                "p90": round(float(np.percentile(vals, 90)), 3),
                "p95": round(float(np.percentile(vals, 95)), 3),
                "max": round(float(np.max(vals)), 3),
                "range": round(float(np.max(vals) - np.min(vals)), 3),
                "zero_or_dry_count": zero_count,
                "zero_percentage": zero_pct,
                "top_peak_events": top_highest,
                "top_lowest_events": top_lowest,
                "distribution_bins": bins,
            }

        # 2. Monthly Breakdown (using primary variable or first variable)
        df["month_year"] = df["dt"].dt.to_period("M").astype(str)
        primary_col = var_cols[0] if var_cols else "value"
        monthly_grp = df.groupby("month_year")[primary_col]
        monthly_breakdown = [
            {
                "period": str(period),
                "count": int(grp.count()),
                "total": round(float(grp.sum()), 2),
                "mean": round(float(grp.mean()), 2),
                "min": round(float(grp.min()), 2),
                "max": round(float(grp.max()), 2),
            }
            for period, grp in monthly_grp
        ]

        # 3. Correlation Matrix (if multiple variables)
        correlation_matrix = None
        if len(var_cols) > 1:
            numeric_df = df[var_cols].dropna()
            if len(numeric_df) > 2:
                correlation_matrix = numeric_df.corr().round(3).to_dict()

        # Primary stats for backward compatibility
        primary_stats = variable_stats.get(primary_col, {})

        return {
            "data_key": data_key,
            "parameter": primary_col,
            "unit": primary_stats.get("unit", "degC" if "temp" in primary_col else "mm" if "precip" in primary_col else ""),
            "parameters": var_cols,
            "primary_parameter": primary_col,
            "location": meta.get("location", "unknown"),
            "provider_id": meta.get("provider_id", "era5"),
            "temporal": temporal,
            "statistics": primary_stats,
            "variables_analysis": variable_stats,
            "correlation_matrix": correlation_matrix,
            "monthly_breakdown": monthly_breakdown,
            "distribution_bins": primary_stats.get("distribution_bins", []),
            "top_peak_events": primary_stats.get("top_peak_events", []),
            "top_lowest_events": primary_stats.get("top_lowest_events", []),
            "zero_or_dry_count": primary_stats.get("zero_or_dry_count", 0),
            "zero_percentage": primary_stats.get("zero_percentage", 0.0),
        }


analysis_service = DatasetAnalysisService()
