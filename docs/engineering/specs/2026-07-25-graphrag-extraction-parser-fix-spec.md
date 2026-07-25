# GraphRAG LLM 抽取 JSON 解析失败修复规格

- 日期：2026-07-25
- 状态：正式 Bug 修复规格；用于承接 GraphRAG 图谱抽取线上失败的诊断、回归测试和最小修复。
- 原计划基线：`docs/engineering/specs/2026-07-24-graphrag-extraction-schema-config-spec.md` 规定图谱抽取失败不得阻断普通文本分块/索引，抽取器应按 Schema 过滤合法实体与三元组。

## 症状偏差

目标知识库的 GraphRAG 索引任务日志出现 `llm output is not a json object`。当前 `extractor.py` 直接对模型文本调用 `json_repair.loads`，当模型返回 JSON 数组、JSON 字符串包裹对象或其他非对象结果时抛出领域错误，导致图谱索引任务失败；目标 Neo4j 范围只有 Segment，没有 Entity/Fact，因此图召回为零。普通文本检索保持 fail-open，不在本修复中改变其语义。

## 修复目标

1. 对模型返回的合法对象、Markdown JSON 代码围栏、带前后解释文本的对象和 JSON 字符串包裹对象进行稳定解析。
2. 只接受最终 JSON 对象作为抽取载荷；数组、标量和完全无 JSON 的输出继续按现有领域错误处理，避免把错误格式静默写入图数据库。
3. 解析后的 payload 继续复用既有 Schema 过滤、属性验证、允许三元组约束和数量上限。
4. 通过单元测试覆盖正向、变体和失败边界；不修改模型选择、Prompt 契约、Neo4j 数据模型、普通文本索引或重试策略。

## 验收标准

- `extract_with_llm` 对上述对象变体返回非空或空数组的 `ExtractionResult`。
- 非对象 JSON 和不可解析文本抛出 `GraphExtractionError`。
- 既有 GraphRAG indexing 单元测试通过，新增核心解析分支覆盖率达到项目要求的替代证据。
- 代码修改范围仅限抽取解析与对应测试；不执行生产数据写入、提交、推送或部署。

## 风险与回滚

- 风险：过宽的对象截取可能误识别模型解释中的示例对象。实现必须优先选择包含 GraphRAG 顶层键的对象，并对候选解析失败继续尝试，不放宽 Schema 过滤。
- 回滚：恢复抽取器与对应测试变更即可；不涉及迁移或数据结构变更。
