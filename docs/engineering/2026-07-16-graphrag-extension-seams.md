# ADR：以独立配置表建立 GraphRAG 扩展边界

日期：2026-07-16

## 背景

Fork 需要为 Dataset 增加可选 GraphRAG 能力，同时保留 Dify 既有向量、全文、检索和 Workflow 协议，并控制后续同步上游的冲突面。

## 决策

Phase 1 采用独立的 `dataset_graph_configs` 表和 `api/core/rag/graph/` 新模块：

- GraphRAG 配置由 `(tenant_id, dataset_id)` 唯一标识，默认关闭。
- Schema、查询和结果对象只依赖 Pydantic 与现有 Python 标准依赖。
- 全局配置放入 `dify_config`，默认 `GRAPH_RAG_ENABLED=false`。
- 本阶段不修改 `datasets`、索引任务、检索入口或 Workflow 节点。

## 备选方案

1. 在 `datasets` 表追加 GraphRAG JSON 字段：拒绝。该表是上游高频模型，升级冲突和数据迁移风险更高。
2. 立即改造 `IndexProcessorFactory` 和 Dataset Retrieval：拒绝。缺少 Outbox、Neo4j Adapter 和降级机制，无法满足失败隔离要求。
3. 仅通过外部插件保存配置：拒绝。后续的索引生命周期 Hook 仍需在 Dify 内部可靠读取 Dataset 配置。

## 后果

- Phase 1 不改变运行行为，能独立回滚：删除新表、模块和配置即可。
- 后续仅在 Phase 2/5 为索引和检索增加少量 Hook；核心 GraphRAG 逻辑保持在新增模块内。
- Graph 数据库依赖和 LlamaIndex 版本在 Phase 3 基于实际 Adapter API 另行锁定，避免提前刷新锁文件。

## 2026-07-19 实现路线修订

Phase 3 已采用 Dify `ModelManager` 完成结构化实体关系抽取，并通过 neo4j 官方 Python Driver 直接写入 Neo4j。第一期继续沿用该直接 Adapter，以减少重新改造成本并优先打通图检索闭环；LlamaIndex/plugin_daemon Adapter 调整为后续可替换实现。

该修订不改变独立模块、独立配置表、普通索引失败隔离和最小上游 Hook 原则。新增约束如下：

- Graph 抽取与 Neo4j 读写仍集中在独立 Graph 模块，不散落到普通索引和 Workflow 节点。
- Dataset Retrieval 只增加调用 Graph Retrieval/Fusion 服务的最小 Hook。
- Neo4j 异常必须 fail-open，基础检索继续可用。
- 后续替换 Adapter 时不得改变 Job、Graph Retrieval 和统一候选结果的领域契约。

