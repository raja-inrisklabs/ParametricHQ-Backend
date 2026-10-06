from typing import Dict, Any, Optional
from pydantic import BaseModel, Field


class ParametricEvaluationRequest(BaseModel):
    data_key: str = Field(..., description="Key of the dataset to evaluate against")
    trigger_condition: str = Field(
        ...,
        description="One of: 'cumulative_below', 'cumulative_above', 'consecutive_dry_days_above', 'max_daily_above', 'mean_above', 'mean_below'"
    )
    threshold: float = Field(..., description="Numerical trigger cutoff threshold")
    payout_limit: float = Field(..., description="Maximum policy payout amount (in USD or base currency)")
    deductible: Optional[float] = Field(0.0, description="Deductible or attachment threshold buffer")


class ParametricEvaluationResult(BaseModel):
    data_key: str
    parameter_type: str
    unit: str
    trigger_condition: str
    threshold: float
    calculated_index_value: float
    triggered: bool
    payout_percentage: float
    payout_amount: float
    total_records: int
    metrics: Dict[str, Any]
    evaluation_summary: str
