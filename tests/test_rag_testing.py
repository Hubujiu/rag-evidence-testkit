import json
from dataclasses import dataclass

import pytest

from rag_evidence_testkit.cache import StageCache, StageKey
from rag_evidence_testkit.contracts import Plan, Suite, digest
from rag_evidence_testkit.examples import examples
from rag_evidence_testkit.runner import run
from rag_evidence_testkit.scoring import evaluate, score_case
from rag_evidence_testkit.semantic import Calibration, review


@pytest.fixture
def sample():
    return examples()


def changed(trace, **values):
    return type(trace).model_validate({**trace.model_dump(), **values})


def test_24_oracle_controls_and_reports(sample, tmp_path):
    suite, plan, traces = sample
    result = run(suite, plan, tmp_path / "run", traces=traces)
    assert result["decision"] == "PASS"
    assert result["total"] == result["counts"]["PASS"] == 24
    assert result["model_calls_recorded"] == 0
    for path in ("report.json", "report.html", "junit.xml", "manifest.json", "state.json"):
        assert (tmp_path / "run" / path).exists()
    with pytest.raises(FileExistsError):
        run(suite, plan, tmp_path / "run", traces=traces)


@pytest.mark.parametrize("family", ["exact", "table", "negation", "entity", "version", "long"])
def test_mutating_visible_text_cannot_pass(sample, family):
    suite, plan, traces = sample
    i = next(i for i, c in enumerate(suite.cases) if c.family == family)
    t = traces[i]
    altered = t.final[0].model_copy(update={"text": t.final[0].text[1:]})
    t = changed(t, final=(altered,), final_context=altered.text,
                request_messages=({"role": "user", "content": altered.text},))
    r = score_case(suite, suite.cases[i], t, plan)
    assert r["outcome"] == "FAIL"
    assert "original_text_mismatch:final" in r["reasons"]


def test_multihop_half_and_loss_location(sample):
    suite, plan, traces = sample
    i = next(i for i, c in enumerate(suite.cases) if c.family == "multihop")
    t = traces[i]
    t = changed(t, final=t.final[:1], final_context=t.final[0].text,
                request_messages=({"content": t.final[0].text},))
    r = score_case(suite, suite.cases[i], t, plan)
    assert r["outcome"] == "FAIL" and r["best_path_coverage"] == .5
    assert r["first_loss_stage"] == "final" and r["evidence_retention"] == .5


def test_or_path_and_adjacent_chunk_union(sample):
    suite, plan, traces = sample
    c = suite.cases[4]
    c = c.model_copy(update={"any_of": ((c.any_of[0][0],), (c.any_of[0][1],))})
    t = traces[4]
    f = t.final[0]
    mid = len(f.text) // 2
    parts = (f.model_copy(update={"end": f.start + mid, "text": f.text[:mid]}),
             f.model_copy(update={"start": f.start + mid, "text": f.text[mid:]}))
    t = changed(t, final=parts, final_context=f.text, request_messages=({"content": f.text},))
    assert score_case(suite, c, t, plan)["outcome"] == "PASS"


@pytest.mark.parametrize("change,reason", [
    ({"version": "old"}, "source_version_or_anchor_mismatch:final"),
    ({"kb": "other"}, "unauthorized_source:final"),
    ({"source_sha256": "0" * 64}, "source_version_or_anchor_mismatch:final"),
    ({"source_id": "unknown"}, "unauthorized_source:final"),
])
def test_identity_and_scope_fail_closed(sample, change, reason):
    suite, plan, traces = sample
    t = changed(traces[0], final=(traces[0].final[0].model_copy(update=change),))
    r = score_case(suite, suite.cases[0], t, plan)
    assert r["outcome"] == "FAIL" and any(v.startswith(reason) for v in r["reasons"])


def test_correct_ids_are_not_final_text(sample):
    suite, plan, traces = sample
    t = changed(traces[0], request_messages=({"content": "answer omitted"},))
    assert "context_absent_from_final_request" in score_case(suite, suite.cases[0], t, plan)["reasons"]


def test_budget_counts_actual_context_with_wrappers(sample):
    suite, plan, traces = sample
    context = traces[0].final_context + "x" * 5000
    t = changed(traces[0], final_context=context, request_messages=({"content": context},))
    assert "context_budget_exceeded" in score_case(suite, suite.cases[0], t, plan)["reasons"]


def test_semantic_is_uncertain_not_fake_pass(sample):
    suite, plan, traces = sample
    f = traces[0].final[0].model_copy(update={"kind": "paraphrase", "text": "令牌过期，请再次登录。"})
    t = changed(traces[0], final=(f,), final_context=f.text, request_messages=({"content": f.text},))
    assert score_case(suite, suite.cases[0], t, plan)["outcome"] == "UNCERTAIN"


def test_failures_missing_and_uncertain_stay_in_denominator(sample):
    suite, plan, traces = sample
    traces[0] = changed(traces[0], status="ERROR", error="timeout")
    result = evaluate(suite, traces[:-1], plan)
    assert result["total"] == 24 and result["pass_rate"] == 22 / 24
    assert result["counts"]["ERROR"] == result["counts"]["NOT_RUN"] == 1
    assert result["decision"] != "PASS"


def test_hard_failure_survives_relaxed_quality_floor(sample):
    suite, plan, traces = sample
    suite = suite.model_copy(update={"cases": tuple(c.model_copy(update={"critical": False}) for c in suite.cases)})
    plan = Plan(suite_hash=digest(suite), minimum_pass_rate=0)
    traces[0] = changed(traces[0], principal="wrong")
    assert evaluate(suite, traces, plan)["decision"] == "FAIL"


def test_frozen_suite_and_duplicate_trace(sample):
    suite, plan, traces = sample
    with pytest.raises(ValueError, match="frozen"):
        evaluate(suite.model_copy(update={"id": "changed"}), traces, plan)
    with pytest.raises(ValueError, match="duplicate"):
        evaluate(suite, traces + traces[:1], plan)


def test_unjudged_precision_is_not_zero(sample):
    suite, plan, traces = sample
    t = changed(traces[-3], stages={**traces[-3].stages, "retrieval": traces[0].final})
    r = score_case(suite, suite.cases[-3], t, plan)
    assert r["ir"]["precision"] is None
    assert r["ir"]["unjudged"]


def test_split_leakage_and_bad_original_rejected(sample):
    suite, _, _ = sample
    raw = suite.model_dump()
    raw["cases"][0]["split"] = "holdout"
    with pytest.raises(ValueError, match="splits"):
        Suite.model_validate(raw)
    raw = suite.model_dump()
    raw["evidence"][0]["quote"] = "wrong"
    with pytest.raises(ValueError, match="anchor"):
        Suite.model_validate(raw)


def key():
    return StageKey(stage="rerank", implementation="code-hash", upstream=("ordered-candidates",),
                    config_hash="config", corpus_hash="corpus", index_snapshot="index", principal="alice",
                    acl_revision="1", model_revision="fixed-model", sample_id="0")


@pytest.mark.parametrize("field", list(StageKey.model_fields))
def test_every_stage_dependency_invalidates_cache(tmp_path, field):
    cache = StageCache(tmp_path)
    k = key()
    cache.put(k, {"value": 1})
    replacement = ("changed",) if field == "upstream" else "changed"
    assert cache.get(k.model_copy(update={field: replacement}), authorize=lambda: True) is None


def test_cache_reauthorize_live_cold_and_integrity(tmp_path):
    cache = StageCache(tmp_path)
    k = key()
    cache.put(k, {"value": 1})
    assert cache.get(k, authorize=lambda: True) == {"value": 1}
    with pytest.raises(PermissionError):
        cache.get(k, authorize=lambda: False)
    for mode in ("cold", "live"):
        assert cache.get(k, authorize=lambda: True, mode=mode, under_test=True) is None
    path = next((tmp_path / "objects").glob("*.json"))
    path.write_text('{"value": 2}', encoding="utf-8")
    with pytest.raises(ValueError, match="integrity"):
        cache.get(k, authorize=lambda: True)


def test_cache_does_not_overwrite_random_draw(tmp_path):
    cache = StageCache(tmp_path)
    cache.put(key(), {"draw": 1})
    with pytest.raises(ValueError, match="output changed"):
        cache.put(key(), {"draw": 2})


def test_live_adapter_receives_no_gold_and_errors_do_not_stop_other_cases(sample, tmp_path):
    suite, _, _ = sample

    @dataclass
    class FailingAdapter:
        implementation = "failure-probe"
        calls: int = 0

        def run(self, request, sources, corpus):
            assert not hasattr(request, "any_of") and not hasattr(request, "expected_behavior")
            assert all(s.id in request.allowed_sources for s in sources)
            self.calls += 1
            raise RuntimeError("diagnostic captured")

    adapter = FailingAdapter()
    result = run(suite, Plan(suite_hash=digest(suite), mode="live"), tmp_path / "live", adapter=adapter)
    assert adapter.calls == 24 and result["counts"]["ERROR"] == 24
    assert "diagnostic captured" in result["rows"][0]["reasons"][0]


def test_review_calibration_and_actual_support_quote():
    rubric = "Determine whether observed text supports the stated fact."
    calibration = Calibration(judge_revision="j1", rubric_hash=digest(rubric), dataset_hash="human-v1",
                              positive_cases=10, negative_cases=10, false_passes=0, false_failures=0,
                              max_false_pass_rate=0, max_false_fail_rate=.1)

    class Budget:
        def reserve(self, key, resources, category):
            return key

        def finish(self, key, **kwargs):
            pass

    kwargs = dict(question="What?", fact="Not allowed", actual_text="Not allowed",
                  judge_revision="j1", rubric=rubric, calibration=calibration, budget=Budget(),
                  reservation_resources={"calls": 1})
    verdict = dict(status="supported", support_quote="Not allowed", reason="explicit")
    result = review(**kwargs, judge=lambda request: verdict)
    assert result["review_required"] and not result["release_eligible"]
    with pytest.raises(ValueError, match="quote"):
        review(**kwargs, judge=lambda request: {**verdict, "support_quote": "invented"})
    kwargs["judge_revision"] = "j2"
    with pytest.raises(ValueError, match="calibration"):
        review(**kwargs, judge=lambda request: verdict)


def test_html_escapes_untrusted_case_names(sample, tmp_path):
    suite, plan, traces = sample
    traces[0] = changed(traces[0], status="ERROR", error="<script>alert(1)</script>")
    run(suite, plan, tmp_path / "escaped", traces=traces)
    page = (tmp_path / "escaped/report.html").read_text("utf-8")
    assert "<script>" not in page and "&lt;script&gt;" in page
    assert json.loads((tmp_path / "escaped/state.json").read_text())["status"] == "COMPLETE"


def test_budget_unknown_failure_and_no_resend(tmp_path):
    from rag_evidence_testkit.budget import BudgetLedger
    ledger = BudgetLedger(tmp_path / "budget.sqlite3", {"calls": 3, "input": 100})
    ledger.reserve("one", {"calls": 1, "input": 60})
    ledger.finish("one", status="UNKNOWN")
    with pytest.raises(ValueError, match="already registered"):
        ledger.reserve("one", {"calls": 1, "input": 60})
    with pytest.raises(ValueError, match="BUDGET_INSUFFICIENT"):
        ledger.reserve("two", {"calls": 1, "input": 60})
    ledger.reserve("two", {"calls": 1, "input": 40}, category="format_retry")
    ledger.finish("two", status="ERROR", usage={"calls": 1, "input": 20}, diagnostic="bad JSON")
    assert ledger.summary()["effective_usage_or_reserved"] == {"calls": 2, "input": 80}
    assert ledger.summary()["unknown_usage_requests"] == 1
    with pytest.raises(ValueError, match="caps"):
        BudgetLedger(tmp_path / "budget.sqlite3", {"calls": 99, "input": 100})


def test_compare_retains_failed_and_missing_pairs(sample):
    from rag_evidence_testkit.comparison import compare
    suite, plan, traces = sample
    baseline = evaluate(suite, traces, plan)
    candidate = evaluate(suite, traces[:-1], plan)
    result = compare(baseline, candidate, resamples=100)
    assert result["total"] == 24 and result["losses"] == 1 and result["ties"] == 23
    assert not result["quality_claim_eligible"]
    with pytest.raises(ValueError, match="identical"):
        compare(baseline, {**candidate, "suite_hash": "other"})


def test_real_context_manager_capture_detects_long_document_truncation(sample):
    from rag_evidence_testkit._context_example import ContextManager
    from rag_evidence_testkit.capture import prepared_trace
    from rag_evidence_testkit.scoring import corpus_hash
    suite, plan, traces = sample
    sources = {s.id: s for s in suite.sources}
    for index, expected in [(0, "PASS"), (20, "FAIL")]:
        case, original = suite.cases[index], traces[index]
        source = sources[original.final[0].source_id]
        prepared = ContextManager().prepare("answer", "Use evidence", {}, [],
                                            [{"document_id": source.id, "version": source.version,
                                              "text": source.text}], case.input.question)
        trace = prepared_trace(request=case.input, corpus_hash=corpus_hash(suite),
                               implementation="product-context-manager", prepared=prepared,
                               sources=sources, stages=original.stages)
        result = score_case(suite, case, trace, plan.model_copy(update={"mode": "live"}))
        assert result["outcome"] == expected, result
        if expected == "FAIL":
            assert "incomplete_final_evidence" in result["reasons"]


def test_untracked_extra_context_and_replay_cannot_claim_live(sample):
    suite, plan, traces = sample
    t = traces[0]
    context = t.final_context + " confidential extra content"
    t = changed(t, final_context=context, request_messages=({"content": context},))
    reasons = score_case(suite, suite.cases[0], t, plan.model_copy(update={"mode": "live"}))["reasons"]
    assert "untracked_context_content" in reasons
    assert "final_request_not_observed" in reasons


def test_budget_atomic_concurrent_reservations(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from rag_evidence_testkit.budget import BudgetLedger
    ledger = BudgetLedger(tmp_path / "concurrent.sqlite3", {"calls": 1})

    def reserve(i):
        try:
            ledger.reserve(str(i), {"calls": 1})
            return True
        except ValueError as exc:
            assert str(exc) == "BUDGET_INSUFFICIENT"
            return False

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(reserve, range(4))) == 1
    assert ledger.summary()["physical_requests"] == 1
