# RAG Evidence Testkit

[![tests](https://github.com/Hubujiu/rag-evidence-testkit/actions/workflows/tests.yml/badge.svg)](https://github.com/Hubujiu/rag-evidence-testkit/actions/workflows/tests.yml)

Evidence-first RAG regression testing: original-source assertions, immutable traces, exact stage caching and zero-provider-call replay.

用原文证据检查 RAG 测试链路，复用已有结果定位错误，减少重复模型调用。适用于已有 Python、HTTP 或 SDK RAG 系统；被测系统通过 `Adapter` 接入。

## 为什么需要它

检索返回了正确文档，不代表生成器最后看到了完整证据。重排、压缩、上下文组装都可能丢掉条件、否定词、表头或多跳链路中的一段。测试本身也可能因缓存过期、失败题被排除或重复付费执行而产生误导。

本项目把问题、金标、执行记录与评分分开，检查以下内容：

- 原文 SHA256、版本、Unicode 偏移及逐字摘录是否一致。
- 最终上下文是否满足至少一条完整证据路径：`[[E1, E2], [E3]]` 表示 `(E1 AND E2) OR E3`。
- 来源、知识库、授权范围、最终请求及上下文预算是否符合合同。
- 证据在哪一阶段丢失，失败、错误、缺失与待复核样本各有多少。
- 改变实现、模型、语料、权限或有序上游产物后，缓存是否正确失效。

默认评分和回放不调用模型。语义裁判为可选接口，需先校准并预留调用预算，输出独立复核记录。

## 快速开始

Python 3.12+，运行依赖仅 Pydantic。

```bash
git clone https://github.com/Hubujiu/rag-evidence-testkit.git
cd rag-evidence-testkit
python -m pip install -e ".[dev]"
python -m pytest -q
python scripts/rag_test.py replay --suite config/suite-v1.json --plan config/plan-v1.json --traces config/oracle-traces-v1.json --output reports/demo-01
```

输出目录必须尚不存在。结果包含 `report.json`、`report.html`、`junit.xml`、逐题 trace、冻结计划、用例与文件哈希清单。CLI 门禁退出码为 PASS=0、FAIL=1、BLOCKED=2。

示例是 **24 条人为构造的评分器正例**，验证评分器能接受合法输入，不代表产品召回质量。反例测试覆盖多跳缺失、否定/表格文本篡改、错误版本、越权来源、缓存失效及预算并发。

## 已测作用与数据边界

历史数据来自企业知识库项目的合成挑战语料；问题和文档是合成的，模型执行与请求账本是真实记录。详见 [效果数据说明](docs/results.md)及[机器可读审计](data/historical-audit.json)。

| 项目 | 实测结果 | 能说明什么 |
|---|---:|---|
| 独立包本地验证 | 43 项测试通过、24 条示例回放通过 | 2026-10-25 全新环境安装验证；远程 CI 状态见徽章 |
| 历史输入 | 168 份原文、48 题、5 方案、240 条结果 | 已有执行记录接入规模 |
| 实际请求一致性绑定 | 240/240 | 结果可追溯到相同问题、上下文及回答 |
| 本轮新增模型 / Embedding 调用 | 0 / 0 | 本轮离线重评复用了已有结果 |
| 已知错误变体 | 6/6 检出 | 选定反例的错误拦截能力 |
| 原工作区框架测试 | 41 项通过 | 提取独立包之前的框架测试记录 |
| 原工作区相关兼容测试 | 168 项通过 | 包含上述 41 项，不能相加 |
| 历史接入及相关回归 | 69 项通过 | 原工作区迁移与组件回归，和前组有重叠 |
| 零调用装配优化对照 | 0 胜、40 平、0 负 | 本次候选未提高完整证据通过数 |

这些数据支持“复用了付费结果、增加自动检查、让排查有统一依据”。目前没有改造前后人工耗时、测试事故发生率的配对测量，**不宣称效率提高某个百分比、节省了特定金额或生产错误减少多少**。独立包的测试数以当前 pytest 输出和 CI 为准。

## 接入自己的 RAG

```python
from pathlib import Path
from rag_evidence_testkit.contracts import Plan, Suite, digest
from rag_evidence_testkit.runner import run

suite = Suite.model_validate_json(Path("my-suite.json").read_text("utf-8"))
plan = Plan(suite_hash=digest(suite), mode="live")
# adapter.run(request, authorized_sources, corpus_hash) -> Trace
report = run(suite, plan, Path("reports/my-run-01"), adapter=adapter)
```

Adapter 只接收输入和授权原文，不接收评分金标。必须在最后一次应用裁剪之后、实际发送模型之前捕获最终请求。适配器本身负责真实身份与权限，不能让被测模型自行声明授权结果。

更多合同及接入说明见 [框架说明](docs/framework.md)。`_context_example.py` 是从原项目提取的无网络上下文组件，用于兼容测试和历史回放；它不代表完整 RAG 服务。

## 重评历史记录

```bash
python scripts/replay_rag_history.py --base /path/to/original-records --legacy /path/to/original-reports --output reports/history-01
```

该脚本针对 `crossdoc-hard` 记录格式，仅通过 SQLite `mode=ro` 读取旧账本。需要原文、问题、片段、原始结果和已有语义复核文件，具体文件名见脚本。仓库发布聚合审计与合成控制样本；原始 provider 账本、密钥、私有业务文档没有随包发布。聚合数据不等于公开可复现的完整历史实验。

## 当前边界

- 当前上下文预算单位为 Unicode codepoints，不能冒充精确 token 限制。
- 原文完整性通过不等于答案正确，表格计算和引用支持还需独立验收。
- 语义裁判提供接口和校准门禁，未内置付费提供商或可直接用于生产的人工校准集。
- 缓存依赖指纹由调用者完整提供；缓存命中不能证明被测阶段已真实执行。
- 此版本没有生产身份系统、原文高亮标注编辑器或完整线上性能基准。
