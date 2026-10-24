# 框架合同与接入

## 验收对象

固定语料、身份、授权修订、问题、历史和上下文预算后，实际交给生成器的上下文必须包含至少一条完整证据路径。
每条路径是 AND，多条路径之间是 OR。返回正确文档 ID、关键词散落、答案碰巧正确都不能替代这一条件。

职责分成五层，分别报告，不合成一个难以解释的总分：

| 层 | 检查对象 | 执行频率 |
|---|---|---|
| L0 数据与实现契约 | 原文哈希、原文偏移、证据引用、权限、输出结构、实现身份 | 每次运行前 |
| L1 检索 | 原文证据召回、来源 Recall/MRR/nDCG、未标注候选 | 每次检索相关修改 |
| L2 最终证据 | AND/OR 完整率、条件/否定/单位、丢失阶段、预算 | 默认主回归 |
| L3 语义和回答 | 摘要事实、回答与引用支持、无答案/追问 | 摘要变更和候选发布 |
| L4 运行行为 | 真 HTTP、当前授权、冷/热延迟、失败/重试、恢复和费用 | 独立发布验收 |

本轮已实现 L0–L2 的执行、评分、报告、缓存、预算账本；L3 提供校准门禁和结构化裁判接口，但没有接通或调用远程裁判，也没有新的人工校准集。L4 沿用已有真实产品契约，未启动付费基准或新的性能测量。

## 数据合同与隔离

`config/{suite,trace,plan}.schema.json` 是导出的 JSON Schema，运行时由 Pydantic 再验证。

- Source：id、kb、version、原文、SHA256。哈希按 UTF-8 原文字节计算。
- Evidence：来源 ID、规范原文 Unicode `[start,end)`、quote、fact、qualifiers。偏移和摘录必须一致；限定词必须存在于摘录中。
- Case：输入与金标分开；`any_of: [[E1,E2],[E3]]` 表达两条合法路径。table 的表头、行标题、单位必须标进证据，不只标数字。
- AdapterInput：只有问题、身份、权限修订、允许来源、历史、日期与预算。执行器只交付授权原文，不向被测系统传 any_of、fact、答案或 qrels。
- Trace：语料、实现、身份、阶段片段、最终 evidence context、实际请求 messages、采集边界、状态与已知成本。
- Plan：完整 suite 哈希、运行模式、必需阶段、切片门槛和 top-k。更改语料、问题或标注后必须建立新计划。

标注单位固定在原文，不依赖 chunk_id。相邻或重叠的已核验原文片段可以合并覆盖证据；不同来源不能拼成同一条原文证据。原文改变时必须重建/迁移标注，不能静默删除失败题。

同一 family 不允许跨 dev/holdout/regression。family 必须由标注者按模板、共享来源或会话依赖定义，框架不能自动证明独立性。真实验收集应在开发前冻结，并由独立权限保存；当前代码的 split 校验不等于访问隔离。

当前 24 例是 **synthetic scorer controls**，涵盖精确项、改写问题、跨章节、多跳、表格、否定、实体、新旧标准、授权、多轮、长文、无答案和拒绝。oracle trace 是人为构造的评分器正例，不是 RAG 实测成绩。真实召回与改写效果必须用适配器重新运行。

## 主指标及状态

主指标：有答案计划题中，来源与上下文契约通过且至少满足一个完整证据 AND 集的题数 / 有答案计划题数。错误和未执行题留在分母。

每题还报告：各阶段命中的 evidence ID、缺失 evidence、首次丢失阶段、最佳路径覆盖比例、从 retrieval 到 final 的保留率。替代路径可能无需召回全部金标，不能把所有替代出处的 recall 当作必须达到 1 的门槛。

IR 指标以 source 为单位，去重后取 top-k；相关 grade 来自明确 qrels。nDCG 对未标注项按零处理但命名为 lower bound；Precision 遇到未标注返回 null，列出 unjudged。完整标注时才可正式比较精确率。语料中没有相关项时 Recall 为 null。

| 状态 | 含义 |
|---|---|
| PASS | 当前测量范围内的证据/行为断言通过 |
| FAIL | 有观测且确定不满足要求 |
| ERROR | 被测执行出错，保留诊断 |
| NOT_RUN | 计划题缺观测或明确未运行 |
| UNCERTAIN | 摘要/改写无法用原文匹配确认，需语义复核 |

最终门禁输出 PASS/FAIL/BLOCKED。权限、来源、原文完整性、上下文预算和观测身份错误不可被降低平均阈值抵消；缺少切片样本、未完成或不确定不能获得 PASS。技术错误会降低端到端通过率，但不能解释为算法质量为零。

`comparison.compare` 要求相同 suite 与 plan、相同逐题集合，给出胜/平/负和 family bootstrap 区间。含 ERROR/NOT_RUN/UNCERTAIN 时禁止完整质量结论；小型合成集的区间不能证明生产稳定领先。

## 采集最终上下文

采集位置必须在去重、重排、压缩、历史拼接、模板填充与最后裁剪之后，紧邻 provider 发送入口。`capture.prepared_trace` 已适配现有 `_context_example.ContextManager` 的 PreparedContext；单元测试真实执行了该组件，确认长文前缀截断会被判失败。

final_context 支持按顺序拼接的原文（连续或双换行），以及 ContextManager 的 JSON evidence 列表。JSON 转义会被正确解码后匹配，预算仍计完整序列化 evidence 区域，包括元数据。禁止在已映射片段旁偷偷附加未映射 evidence 文本。自定义上下文格式需要显式适配，不能只传检索返回值。

主运行器 SDK：

```python
from pathlib import Path
from rag_evidence_testkit.runner import run
from rag_evidence_testkit.contracts import Plan, digest

# adapter.run(request, authorized_sources, corpus_hash) -> Trace
# run 内部不会将 Suite/Case 或金标传给 adapter。
result = run(suite, Plan(suite_hash=digest(suite), mode="live"),
             Path("reports/rag-testing/new-unique-run"), adapter=adapter)
```

适配器是可信观测边界，不能由被测模型自己填写授权结果。必须包含所有实际发送的证据，包括恢复的历史工具证据；历史授权还需沿用产品 EvidenceGateway。当前 capture helper 覆盖 projection.evidence，不自行验证恢复历史工具和 provider 服务端裁剪。若这些部分参与答案，应扩展对应 trace/契约后再宣布该范围通过。

当前预算只支持明确命名的 Unicode codepoints。它不冒充 tokenizer 的精确 token 数，也不替代旧运行的 4000-token 条件。不同预算单位不得直接比较；模型窗口限制仍由原产品控制。

## 缓存：复用产物，重新判定

StageCache 用 SQLite 保存映射，文件保存内容寻址 JSON。读时验证完整哈希并执行当前授权回调。缓存键包含：stage、实现依赖指纹、有序上游哈希、配置、语料、索引快照、principal、ACL 修订、模型修订和 sample_id。

| 变更 | 失效范围 |
|---|---|
| 原文、解析器、切块 | 受影响准备产物及其依赖；索引快照变化后全部查询重评 |
| 查询、历史、改写提示 | 查询产物及其下游 |
| 候选内容或顺序、重排器 | 重排及其下游 |
| 压缩、模板、预算 | 最终上下文及其下游 |
| 金标、rubric、judge | 重新评分，可复用未变化的 trace |
| 权限 | 立即阻断旧授权读取，并使用新的权限键 |

StageKey 只提供严格合同，不会自动推断源码依赖。调用者应将相关源码/依赖文件的内容哈希填入 implementation，把输入/历史/问题等编码进上游哈希；未知依赖保守失效。禁止仅填一个永不变的版本标签。当前主评分器每次重新执行，不缓存 PASS 判定。

replay 只说明录制产物在当前规则下的结果；live 时被测阶段必须 `under_test=True` 绕过缓存；cold 全部绕过。模型变动/波动测量使用独立 sample_id。提供商 prompt cache 与应用缓存分别核账。缓存不负责自动恢复远程请求。

## AI 与费用

`semantic.review` 只把问题、一个必要事实、实际文本、固定 rubric 交给裁判，输出 supported/contradicted/insufficient/uncertain、支持原文与理由。校准数据哈希、模型 revision 和 rubric 必须匹配；supported 必须给出真实存在于实际文本的引文。

这仍不能证明语义判断正确：校准要用独立人工正例、负例、边界例，记录假通过/假失败。当前接口的输出是独立 sidecar，`release_eligible=false`，不会自动推翻确定性失败或编辑 gold。复核接受的等价表述应进入新标注版本，再统一重评所有方案。按固定种子分层抽查程序 PASS，避免只看疑难失败。

BudgetLedger 用 `BEGIN IMMEDIATE` 原子预留物理请求。资源维度和上限冻结；同一请求 ID 永不重复发送。ERROR/UNKNOWN 以及缺失 token 使用原预留额，不能按零算。返回的实际 usage 即使超过预留仍如实记账，并阻止后续超额调用。COMPLETE/ERROR/UNKNOWN 终态只读，修复使用新请求身份并关联原请求，不覆盖历史。

主 runner 不启动远程模型，预算仅在显式接入 provider/裁判时使用。它不自动预测完整大实验工作量，不会替代 v7 的已有费用总账。若未来迁移，应先导入所有旧实际费用和未知预留，核账后再启用新调用。

准备、查询、生成、judge、格式重试分开记账；美元单价未知时保持未知。延迟需要真实在线交错配对请求，不能从 replay wall time 或首次缓存命中得出性能收益。

## 使用与报告

在项目根目录运行；每次选择新的输出目录，旧目录存在会拒绝覆盖。

```powershell
# 框架自身：不启动 Docker，不请求模型。
.venv\Scripts\python.exe -m pytest tests/test_rag_testing.py -q

# 验证用例并冻结计划；输出文件必须尚不存在。
.venv\Scripts\python.exe scripts/rag_test.py freeze `
  --suite config/suite-v1.json --output .local/my-rag-plan.json

# 评分器正例演示（不是产品检索分数）。
.venv\Scripts\python.exe scripts/rag_test.py replay `
  --suite config/suite-v1.json --plan config/plan-v1.json `
  --traces config/oracle-traces-v1.json --output reports/rag-testing/my-controls-01
```

CLI 退出码：PASS=0，FAIL=1，BLOCKED=2；输入格式/冻结合同错误直接报错并返回非零。
每个 run 保留 suite、plan、逐题 traces、report.json、junit.xml、report.html、manifest.json 和 state.json。
错误与不确定在 JUnit 中分别表现为 error/skipped，最终门禁仍以 report 为准，不能将 pytest skipped 当成通过。

报告包含原始测试问题与上下文。业务材料应放受控目录；HTML 转义所有文本且不加载外部脚本。当前 HTML 展示逐题结构化诊断，尚无原文高亮标注编辑器。

