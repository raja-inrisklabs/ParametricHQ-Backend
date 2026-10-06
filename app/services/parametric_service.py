from typing import Dict, Any
import pandas as pd
import numpy as np

from app.core.exceptions import PlatformException
from app.schemas.parametric import ParametricEvaluationRequest, ParametricEvaluationResult
from app.services.storage_service import storage_service


class ParametricService:
    """
    Parametric Insurance Index & Trigger Evaluation Engine.
    Executes objective trigger verification on analytical climate datasets (Parquet).
    """

    @classmethod
    def evaluate(cls, req: ParametricEvaluationRequest) -> ParametricEvaluationResult:
        df = storage_service.read_parquet(req.data_key)
        if df.empty:
            raise PlatformException(f"Dataset '{req.data_key}' contains no records to evaluate.", status_code=400)

        param = str(df["parameter"].iloc[0]) if "parameter" in df.columns else "unknown"
        unit = str(df["unit"].iloc[0]) if "unit" in df.columns else ""
        condition = req.trigger_condition.lower()
        threshold = req.threshold
        payout_limit = req.payout_limit
        deductible = req.deductible or 0.0

        values = df["value"].to_numpy()
        total_sum = float(np.sum(values))
        max_val = float(np.max(values))
        min_val = float(np.min(values))
        mean_val = float(np.mean(values))

        # Consecutive dry days (< 1.0 mm)
        max_dry_streak = 0
        current_dry_streak = 0
        dry_threshold = 1.0
        for val in values:
            if val < dry_threshold:
                current_dry_streak += 1
                if current_dry_streak > max_dry_streak:
                    max_dry_streak = current_dry_streak
            else:
                current_dry_streak = 0

        triggered = False
        calculated_index = 0.0
        payout_percentage = 0.0

        if condition == "cumulative_below":
            # Drought index: rainfall deficit
            calculated_index = total_sum
            if calculated_index < threshold:
                triggered = True
                # Scale deficit payout: max payout when index reaches deductible
                deficit = threshold - calculated_index
                max_deficit = max(1.0, threshold - deductible)
                payout_percentage = min(1.0, max(0.0, deficit / max_deficit))

        elif condition == "cumulative_above":
            # Flood / excess rain
            calculated_index = total_sum
            if calculated_index > threshold:
                triggered = True
                excess = calculated_index - threshold
                payout_percentage = min(1.0, excess / (threshold * 0.5 + 1e-6))

        elif condition == "consecutive_dry_days_above":
            # Dry spell duration
            calculated_index = float(max_dry_streak)
            if calculated_index >= threshold:
                triggered = True
                excess_days = calculated_index - threshold
                payout_percentage = min(1.0, (excess_days + 1) / 10.0)

        elif condition == "max_daily_above":
            # Flash flood / extreme wind / single-day extreme
            calculated_index = max_val
            if calculated_index >= threshold:
                triggered = True
                payout_percentage = 1.0  # Binary trigger

        elif condition == "mean_above":
            # Heatwave index
            calculated_index = mean_val
            if calculated_index > threshold:
                triggered = True
                payout_percentage = min(1.0, (calculated_index - threshold) / 5.0)

        elif condition == "mean_below":
            # Frost / cold wave
            calculated_index = mean_val
            if calculated_index < threshold:
                triggered = True
                payout_percentage = min(1.0, (threshold - calculated_index) / 5.0)

        else:
            raise PlatformException(
                f"Unsupported trigger condition '{condition}'. Supported: "
                "['cumulative_below', 'cumulative_above', 'consecutive_dry_days_above', "
                "'max_daily_above', 'mean_above', 'mean_below']",
                status_code=400
            )

        payout_amount = round(payout_percentage * payout_limit, 2)
        payout_percentage = round(payout_percentage * 100.0, 2)

        summary_msg = (
            f"Parametric trigger {'ACTIVATED' if triggered else 'NOT ACTIVATED'}. "
            f"Calculated index: {calculated_index:.2f} {unit} vs threshold {threshold:.2f} {unit}. "
            f"Payout: ${payout_amount:,.2f} ({payout_percentage:.1f}% of ${payout_limit:,.2f} limit)."
        )

        return ParametricEvaluationResult(
            data_key=req.data_key,
            parameter_type=param,
            unit=unit,
            trigger_condition=condition,
            threshold=threshold,
            calculated_index_value=round(calculated_index, 3),
            triggered=triggered,
            payout_percentage=payout_percentage,
            payout_amount=payout_amount,
            total_records=len(df),
            metrics={
                "cumulative_sum": round(total_sum, 2),
                "max_value": round(max_val, 2),
                "min_value": round(min_val, 2),
                "mean_value": round(mean_val, 2),
                "max_consecutive_dry_days": max_dry_streak,
            },
            evaluation_summary=summary_msg,
        )


parametric_service = ParametricService()
