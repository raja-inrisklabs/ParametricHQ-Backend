from fastapi import APIRouter
from app.schemas.parametric import (
    ParametricEvaluationRequest,
    ParametricEvaluationResult
)
from app.services.parametric_service import parametric_service

router = APIRouter(prefix="/parametric", tags=["Parametric Triggers"])


@router.post("/evaluate", response_model=ParametricEvaluationResult)
def evaluate_policy_trigger(req: ParametricEvaluationRequest):
    """
    Evaluate a parametric insurance policy trigger against a verified dataset.
    Calculates trigger exceedance, consecutive dry days, cumulative deficits,
    and resulting payout percentages and amounts.
    """
    return parametric_service.evaluate(req)
