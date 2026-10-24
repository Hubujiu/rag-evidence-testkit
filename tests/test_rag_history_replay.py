import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

from rag_evidence_testkit.contracts import text_hash


spec = importlib.util.spec_from_file_location('rag_history_replay', Path(__file__).parents[1] / 'scripts/replay_rag_history.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.mark.parametrize('response_matches', [True, False])
def test_request_binding_is_exact_and_old_inputs_are_unchanged(tmp_path, response_matches):
    base, old = tmp_path / 'base', tmp_path / 'old'
    base.mkdir()
    old.mkdir()
    text = '§规则：设备运行时不允许开盖。'
    passage = dict(doc_id='D1', text=text, start=0, end=len(text), unit_id='U1')
    prediction = dict(answer='不允许', abstain=False)
    files = {
        base / 'sources.json': [dict(id='D1', text=text)],
        base / 'units.json': [dict(id='U1', **passage)],
        base / 'questions.json': [dict(id='Q1', question='运行时能开盖吗？', gold=['D1'],
                                        answerable=True, group='g1', family='negation', tier='short')],
        old / 'result-navigation-repaired.json': {'rows': [dict(method='hybrid', question_id='Q1',
            context=[passage], prediction=prediction, technical_error=False, retrieved_units=['U1'],
            score=1, evidence_tokens=40)]},
        old / 'review-result.json': {'rows': [dict(method='hybrid', qid='Q1', technical_error=False,
                                                  verdict=dict(reference_correct=True))]},
    }
    for path, value in files.items():
        path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    dbpath = base / 'providers' / 'calls' / 'ledger.sqlite3'
    dbpath.parent.mkdir(parents=True)
    request = dict(purpose='hard_answer', owner_scope='hybrid/Q1/answer', payload={'messages': [
        dict(role='user', content=json.dumps(dict(question='运行时能开盖吗？', passages=[passage]), ensure_ascii=False))]})
    with sqlite3.connect(dbpath) as db:
        db.execute('CREATE TABLE jobs (key TEXT,status TEXT,request_json TEXT,result_json TEXT,usage_json TEXT)')
        db.execute('INSERT INTO jobs VALUES (?,?,?,?,?)', ('k1', 'COMPLETE', json.dumps(request),
                   json.dumps({'value': prediction if response_matches else {'answer': '允许'}}), '{}'))
    before = {p: text_hash(p.read_bytes().hex()) for p in [*files, dbpath]}
    result = module.migrate(base, old, tmp_path / 'out')
    assert result['requests_bound'] == int(response_matches)
    assert result['new_provider_calls'] == 0
    if not response_matches:
        assert result['methods']['hybrid']['framework_counts']['FAIL'] == 1
    assert all(text_hash(p.read_bytes().hex()) == sha for p, sha in before.items())
