"""Small synthetic scorer controls, explicitly not a production quality benchmark."""
from .contracts import AdapterInput, Case, Evidence, Fragment, Plan, Source, Suite, Trace, digest, text_hash
from .scoring import corpus_hash


def examples():
    specifications = [
        ("exact", "E401 错误如何处理？", ["E401 表示令牌失效，必须重新登录。"]),
        ("paraphrase_query", "员工在沪住店能报多少？", ["普通员工在上海住宿，每晚上限为600元。"]),
        ("cross_section", "登记完成以后做什么？", ["登记完成后应按附录乙执行。", "附录乙要求先断电，再进行密封检查。"]),
        ("multihop", "甲项目能用哪种材料？", ["甲项目适用乙规范。", "乙规范要求使用丙材料。"]),
        ("table", "上海普通员工住宿标准？", ["城市|职级|住宿上限（元/晚）\n上海|普通员工|600"]),
        ("negation", "设备运行时能否开盖？", ["设备运行时不允许开盖；必须断电后操作。"]),
        ("entity", "A2 的额定电压？", ["产品A2的额定电压为24伏；产品A1为12伏。"]),
        ("version", "2026年补贴多少？", ["2026年有效标准：每人每天80元，替代2025年的60元。"]),
        ("acl", "公开手册的维修窗口？", ["公开手册：维修窗口为每周六09:00至11:00。"]),
        ("history", "它的保修期多久？", ["产品B3保修期为24个月。"]),
        ("long", "中段规定的报送期限？", ["本章要求所有正式报表在次月5日前报送。"]),
    ]
    sources, evidence, cases, fragments = [], [], [], {}
    for name, question, quotes in specifications:
        group = []
        for i, quote in enumerate(quotes):
            sid = f"{name}-{i}"
            prefix = ("日常巡检记录不规定报送期限。\n" * 150) if name == "long" else "背景说明。\n"
            text = prefix + quote + "\n附件结束。"
            s = Source(id=sid, kb="kb", version="2026-01", text=text, sha256=text_hash(text))
            e = Evidence(id="e-" + sid, source_id=sid, start=len(prefix), end=len(prefix) + len(quote),
                         quote=quote, fact=quote)
            sources.append(s)
            evidence.append(e)
            group.append(e.id)
            fragments[e.id] = Fragment(source_id=sid, source_sha256=s.sha256, version=s.version,
                                       kb=s.kb, start=e.start, end=e.end, text=quote)
        for variant in range(2):
            request = AdapterInput(case_id=f"{name}-{variant}", question=question + ("请核对适用条件。" if variant else ""),
                                   kb="kb", principal="employee", acl_revision="acl-1",
                                   allowed_sources=tuple(e.source_id for e in evidence), as_of="2026-10-24",
                                   history=({"role": "user", "content": "我正在咨询产品B3。"},) if name == "history" else (),
                                   context_budget=4096)
            cases.append(Case(input=request, family=name, slices=(name,), any_of=(tuple(group),),
                              qrels={fragments[e].source_id: 1 for e in group}))
    for behavior in ("abstain", "deny"):
        cases.append(Case(input=AdapterInput(case_id=behavior, question="没有授权资料的问题？", kb="kb",
                                             principal="employee", acl_revision="acl-1", allowed_sources=(),
                                             as_of="2026-10-24", context_budget=4096),
                          family=behavior, slices=(behavior,), expected_behavior=behavior))
    suite = Suite(id="synthetic-scorer-controls-v1", label_provenance="synthetic_engineering",
                  sources=tuple(sources), evidence=tuple(evidence), cases=tuple(cases))
    traces = []
    for c in cases:
        final = tuple(fragments[e] for e in c.any_of[0]) if c.any_of else ()
        context = "\n\n".join(f.text for f in final)
        traces.append(Trace(case_id=c.input.case_id, corpus_hash=corpus_hash(suite),
                            implementation="synthetic-oracle-control/1", principal=c.input.principal,
                            acl_revision=c.input.acl_revision, capture="replay", final=final,
                            final_context=context, request_messages=({"role": "user", "content": context},),
                            stages={stage: final for stage in ("retrieval", "rerank", "assembly")},
                            behavior=c.expected_behavior, input_tokens=0, output_tokens=0))
    return suite, Plan(suite_hash=digest(suite)), traces
