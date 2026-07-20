# Dify GraphRAG Phase 1 原始需求

Last Updated: 2026-07-19

## 来源与状态源

- 来源计划：`D:\Downloads\dify-graphrag-minimal-invasive-fork-development-guide.md`。
- 本文件与 `docs/engineering/plans/2026-07-16-graphrag-plan.md` 是本轮唯一状态源。
- 用户要求：分析计划、生成可落地文档并实施。

## 实现状态校正（2026-07-19）

- Phase 2 的 Graph Job、Reconciler、Celery Beat 注册和 `graph_index` 队列已经实现，原“未实现”状态失效；当前仍存在提交前派发、恢复后不重新派发、领取非原子和最大重试未生效等可靠性缺口。
- Phase 3 的 LLM 抽取与 Neo4j 写入已经实现。实际路线是 Dify `ModelManager` + 自定义结构化抽取 + neo4j 官方 Python Driver，没有使用 LlamaIndex 或 `plugin_daemon` 中的 `llamaindex_neo4j_graph`。
- Graph Retrieval、Dataset Retrieval 融合、fail-open、文档/Segment 删除同步、状态运维和真实 Neo4j E2E 尚未实现或验证，GraphRAG 当前不能参与实际知识库问答。
- 后续阶段和完成标准以 `docs/engineering/plans/2026-07-16-graphrag-plan.md` 的 P0/P1 计划为准；本文件中“本轮范围”保留为 Phase 1 的历史需求边界。

## 已确认基线

- Fork 当前基线为 Dify `1.15.0`，提交 `9c61f3300b2a48d7f11349e7f42ac5d530b3f78d`。
- `origin` 是 `github714801013/dify_neo4j`，`upstream` 是 `langgenius/dify`；本轮不执行上游同步。
- GraphRAG 相关入口尚未实现，仓内没有 LlamaIndex 或 Neo4j Python 依赖。
- 索引调用集中在 `IndexProcessorFactory`，由文档和手工分段索引任务调用；检索统一入口为 `api/core/rag/retrieval/dataset_retrieval.py`。
- GitNexus 未索引本仓，调用链定位以本地源码检索为证据。
- Docker 的 API/worker 共享配置已通过 `env_file` 引入 `docker/.env`；该文件是 Docker 运行时 Neo4j 连接信息的唯一事实源。

## 本轮范围：Phase 1（含 Dataset 页面配置入口）

本轮只实现 GraphRAG 的领域与配置基础，不接入运行链路：

1. 新增 Graph Schema、Graph Query、Graph Result 与统一候选结果领域对象。
2. 新增 Dataset 级 GraphRAG 配置模型和可逆数据库迁移。
3. 新增默认关闭的全局 GraphRAG 环境配置及其校验。
4. 为 Schema、配置默认值和模型约束补充单元测试。
5. 在 Dataset 设置页提供 GraphRAG 开关，读取并保存 Dataset 级配置。

## 明确不在本轮范围

- 不新增 LlamaIndex、Neo4j 或其他第三方依赖，不修改 `api/uv.lock`。
- 不修改 `IndexProcessorFactory`、索引任务、Dataset Retrieval、Workflow 节点或 Reranker。
- 不写 Outbox、Celery Graph Worker、Neo4j 查询、图抽取或图检索。
- 不提交、不推送；用户已明确授权使用 `docker/deploy.sh` 部署当前工作区。

## 领域与隔离约束

- `GraphSchema` 必须校验实体类型、关系类型和三元组；三元组只能引用已声明的类型。
- Dataset Graph 配置必须以 `(tenant_id, dataset_id)` 唯一，默认 `enabled=false`。
- 配置保存 `schema_json` 和可选模型配置，但 Phase 1 不读取密钥、不调用 LLM。
- 全局开关 `GRAPH_RAG_ENABLED=false`；即使数据库存在已启用配置，Phase 1 也不改变现有索引或检索行为。
- Neo4j URI、用户名、密码和数据库名只能从未托管的 `.env` 读取；`.env.example` 只声明空白变量契约，不包含实例地址或凭据。
- `docker/deploy.sh` 生成并上传远端运行时 `.env` 时，必须保留全部 GraphRAG 与 Neo4j 变量，不能只保留本地 `docker/.env` 中的值。
- 为完成本轮部署，Web Docker 构建必须显式安装 workspace 开发依赖；这仅影响构建阶段，不改变运行时镜像依赖或 GraphRAG 运行行为。
- 本地 API/Web 镜像构建不得向 BuildKit daemon 或 Docker build arg 注入通用 `HTTP_PROXY`、`HTTPS_PROXY`、`NO_PROXY` 变量；运行时环境变量的传递策略不在本项变更范围。
- 本地 Docker Hub 基础镜像解析使用未托管 `docker/.env` 中配置的国内 BuildKit registry mirror；`.env.example` 不固化环境专属镜像地址。
- 本地 API/Web 镜像构建可分别从未托管 `docker/.env` 读取 `UV_INDEX_URL` 和 `NPM_CONFIG_REGISTRY`，用于 PyPI 与 npm 依赖下载；仅在非空时作为 build arg 传入，不影响运行时容器环境。
- 不改变 Dify 既有 Dataset、Document、DocumentSegment 表结构和现有外部协议。

## 本轮补充需求（2026-07-17）

- 图配置区域的交互与视觉组织应参考现有“检索设置”组件：复用既有参数控件的数字输入、步进、滑块、禁用态和布局风格，避免继续使用普通裸 `<input>` 作为主要数值配置控件。
- 图配置字段仍保持现有业务语义、默认值、取值范围和提交契约不变；本次只调整前端呈现与交互复用。
- 图片 `codex-clipboard-3842afc6-b9b8-4904-8009-a6ef0d5ba010.png`：可见事实为用户要求将“图配置”按“检索设置”的组件方式实现；图片文字与布局细节因当前运行环境不支持像素级视觉读取，不能作为额外字段契约依据。

## 验收标准

- 默认 Graph Schema 可构造，非法空类型、重复类型、未声明三元组成员会被拒绝。
- Graph Query 的深度、数量与方向约束可验证。
- Dataset Graph 配置模型默认为关闭，并由迁移建立独立表、唯一约束和查询索引；降级可删除该表。
- 默认环境配置加载后 GraphRAG 关闭，超时、并发、重试值均为正数。
- Dataset 设置页显示“启用 GraphRAG”开关；开关初始值来自 Dataset 级配置，保存后刷新页面仍保持该值。
- 未启用 GraphRAG 时不改变既有索引和检索行为；页面开关仅保存配置，不绕过全局 `GRAPH_RAG_ENABLED`。
- Docker Compose 使用 `docker/.env` 解析后，API/worker 可获得 GraphRAG 与 Neo4j 变量；示例文件不包含真实连接信息。
- 使用 `docker/deploy.sh` 部署后，远端 `docker/.env` 包含 GraphRAG 与 Neo4j 变量，且 API 健康检查成功。
- Web Docker 镜像可完成 `pnpm build && pnpm build:vinext`，不再出现 `@base-ui/react/*` 或 `jotai` 模块解析失败。
- 定向单元测试、Ruff 检查和新增迁移的 Python 语法检查通过。

## 前端影响

- Dataset 详情接口增加 `graph_rag_enabled` 字段；Dataset 更新接口接受同名字段并写入独立配置表。
- Dataset 设置页在索引设置区域增加可编辑开关，并沿用现有保存按钮与权限控制。
- Dataset 创建页 Step Two 也必须提供同一套 GraphRAG 配置，创建请求直接持久化配置。
- 创建阶段 Graph Schema/抽图配置校验失败时必须中断创建及后续索引流程，不发送创建文档请求；设置阶段保存失败时不覆盖原配置。
- 抽图模型必须关联 Dify 当前租户已配置的模型供应商和模型列表，禁止自由输入未配置模型。
- 前端实现说明：`docs/engineering/2026-07-16-graphrag-frontend-implementation.md`。
- 抽图关键配置必须以结构化字段暴露，至少包括抽图模型提供商、模型名称、温度、每个 Segment 最大三元组数和严格 Schema 校验；不要求用户编辑原始 JSON。
- `extract_model_config` 可以继续以 JSON 存储，但由结构化字段生成和校验；配置字段超出范围时停止保存，不能继续提交其他 Dataset 字段。
- Graph Schema 继续由后端领域对象校验和固定 Schema 管理，页面不提供任意 Schema JSON 编辑入口。

## 正式架构方案（2026-07-17 已确认）

本节覆盖后续图索引运行链路，并作为 Phase 2 及之后阶段的正式设计约束。

### 架构决策

采用“独立 Graph Indexing 模块 + 独立 Job 状态表 + Reconciler 补偿 + 独立 Graph Worker + Adapter 隔离插件/Neo4j”的方案。

- 第一阶段允许分钟级最终一致，不要求配置保存后同步完成图索引。
- 首次启用 Dataset GraphRAG 时，自动分批回填已有 `completed` 文档，不在配置保存请求内同步扫描或执行。
- 优先复用 `plugin_daemon` 中的 `llamaindex_neo4j_graph`，通过 Adapter 调用，不新增 Python Neo4j/LlamaIndex 依赖；正式编码前必须先核对真实 Tool/RPC 契约。
- 第一阶段提供后端 Job 状态、日志和运维入口；前端状态展示作为后续阶段能力。
- 文档删除或归档必须生成删除任务，同步清理对应图数据，避免脏图。
- 图索引算法或插件升级通过 `graph_version` 控制重建，不自动立即触发全量重建；支持按 Dataset/Document 分批重建。

### 触发原则

Reconciler 是正确性保障，不依赖普通索引任务名或内部调用顺序：

```text
GraphRAG 已启用的 Dataset
    -> completed 文档
    -> 计算 source_version
    -> 检查 graph_index_jobs
    -> 创建幂等 Job
    -> 投递 graph_index 队列
```

配置保存只保存配置，不直接执行抽取、Neo4j 写入或大量任务投递。后续如确有实时性要求，可增加一个只调用 Coordinator 的最小索引完成 Hook，但仍必须保留 Reconciler 兜底。

### 独立代码边界

新增代码集中在 `api/core/rag/graph_indexing/`，包含领域对象、Coordinator、Reconciler、Task、Repository、Ports、版本计算、错误映射和 Adapter。普通索引、检索、Workflow、Reranker 和 `IndexProcessorFactory` 不作为第一期修改目标。

### Job 与幂等

新增独立表 `dataset_graph_index_jobs`，至少保存租户、Dataset、Document、`source_version`、`graph_version`、状态、重试次数、锁定时间和最近错误。唯一键为：

```text
(dataset_id, document_id, source_version, graph_version)
```

任务状态为：

```text
pending -> running -> succeeded
                 -> retry_waiting -> pending
                 -> failed
pending/running -> stale 或 cancelled
```

任务领取必须使用原子状态变更或行锁；Reconciler 必须恢复超时的 `running` Job，避免 Worker 崩溃造成永久卡单。

### 失败隔离与开关

建议拆分以下全局开关：

```text
GRAPH_RAG_ENABLED
GRAPH_INDEXING_ENABLED
GRAPH_INDEX_RECONCILE_ENABLED
```

图索引使用独立 `graph_index` 队列。插件、Neo4j、网络和数据库暂时性异常可重试；非法数据、权限错误和超过次数的失败进入 `failed`。任何图索引失败都不能回滚或阻塞普通向量索引。

### 升级与回滚约束

- 只新增 Graph Job 表及索引，不修改 `datasets`、`documents`、`segments` 等上游高频表。
- Alembic migration 必须独立、可逆，`downgrade` 只删除本次新增对象。
- 不把 GraphRAG 逻辑散落到普通索引任务；Beat 只增加独立 Reconciler 注册。
- 旧版本没有 Graph Job 表或 Graph Worker 时，普通索引和基础检索必须继续工作。
- 回滚应用版本前关闭 Graph Indexing/Reconciler，保留任务表作为审计数据，不强制删除。
- 后续升级验证必须比较 `upstream` 基线，确认本地新增模块、迁移和最小注册点仍可独立识别和回滚。

### 正式验收补充

- 同一文档同一 `source_version + graph_version` 重复扫描只产生一个 Job。
- 文档内容或分段版本变化后生成新 Job，旧 Job 可标记 `stale`。
- 首次开启 GraphRAG 能按批次回填历史 `completed` 文档，并受限流参数保护。
- 插件或 Neo4j 不可用时 Job 进入重试/失败，普通检索仍可用。
- 文档删除/归档后对应图数据可被删除。
- Graph Worker 崩溃后，超时 Job 能被 Reconciler 恢复。
- GraphRAG 算法版本变化可通过 `graph_version` 触发可控重建。
