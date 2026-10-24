"""Optional calibrated semantic reviewer. Results are sidecars, never silent gold edits."""
from typing import Literal

from pydantic import Field

from .contracts import Contract, digest


class Verdict(Contract):
    status: Literal["supported", "contradicted", "insufficient", "uncertain"]
    support_quote: str
    reason: str


class Calibration(Contract):
    judge_revision: str
    rubric_hash: str
    dataset_hash: str
    positive_cases: int = Field(gt=0)
    negative_cases: int = Field(gt=0)
    false_passes: int = Field(ge=0)
    false_failures: int = Field(ge=0)
    max_false_pass_rate: float = Field(ge=0, le=1)
    max_false_fail_rate: float = Field(ge=0, le=1)

    def admitted(self):
        return (self.false_passes / self.negative_cases <= self.max_false_pass_rate
                and self.false_failures / self.positive_cases <= self.max_false_fail_rate)


def review(*, question, fact, actual_text, judge, judge_revision, rubric, calibration, budget,
           reservation_resources):
    """Caller owns a measured provider and its persistent budget ledger.

    budget.reserve() must atomically reserve before sending; unknown outcomes stay
    reserved. No retries on low scores, no credentials or vendor client in this module.
    """
    if (not calibration.admitted() or calibration.judge_revision != judge_revision
            or calibration.rubric_hash != digest(rubric)):
        raise ValueError("judge calibration does not match the frozen reviewer")
    request = {"question": question, "fact": fact, "actual_text": actual_text,
               "rubric": rubric, "judge_revision": judge_revision}
    reservation = budget.reserve(digest(request), reservation_resources, category="judge")
    received = False
    try:
        raw = judge(request)
        received = True
        result = Verdict.model_validate(raw)
        if result.status == "supported" and (not result.support_quote or result.support_quote not in actual_text):
            raise ValueError("semantic support quote absent from observed text")
    except Exception as exc:
        budget.finish(reservation, status="ERROR" if received else "UNKNOWN",
                      diagnostic=f"{type(exc).__name__}: {exc}")
        raise
    budget.finish(reservation, status="COMPLETE")  # no provider usage reported: reservation stays charged
    return {"request_hash": digest(request), "calibration_hash": digest(calibration),
            "reservation": reservation, "verdict": result.model_dump(),
            "release_eligible": False, "review_required": True}
