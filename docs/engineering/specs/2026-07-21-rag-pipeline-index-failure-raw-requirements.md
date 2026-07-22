# 知识库 Pipeline 索引失败状态修复

## 阶段状态

- `grill-with-docs`: complete
- `to-spec`: complete
- `to-tickets`: complete
- `stage_status`: in_progress
- `tdd + implement`: complete for Ticket 01、02
- `code-review`: complete
- `stage_status`: awaiting
- `next_skill`: deployment
- `continuation_mode`: authorization
- `resume_from`: 本文“已确认决策”和“验收标准”
- `spec`: `docs/engineering/specs/2026-07-21-rag-pipeline-index-failure-spec.md`
- `tickets`: `.scratch/rag-pipeline-index-failure/issues/01-runtime-default-summary-prompt.md`, `.scratch/rag-pipeline-index-failure/issues/02-pipeline-error-state.md`, `.scratch/rag-pipeline-index-failure/issues/03-recover-and-verify-current-document.md`
- `required_action`: 手动调用 `$implement`，按 Ticket 01 → Ticket 02 顺序执行；Ticket 03 需另行取得部署和生产状态写入授权。
- `implementation_evidence`: Ticket 01、02 已实现并通过定向单元测试；另修复 QA 索引模型异常被吞掉导致的假成功；Ticket 03 尚未执行。
- `review_evidence`: Standards 与 Spec 两轴复审无问题。
- `test_evidence`: 原有定向测试 132 passed；本轮 QA 索引处理器及相关 Pipeline/RAG 测试 148 passed，两个修改代码文件 Ruff check passed。
- `blocked_next_action`: Ticket 03 需要部署授权，以及仅针对目标文档的生产状态写入授权。

## 原始需求

新增文件后，页面长期显示“排队中”。生产取证确认：任务已进入 `priority_pipeline` 队列并被 Worker 消费，但发布版知识库 Pipeline 在创建 `knowledge-index` 节点时因 `summary_index_setting.summary_prompt` 为 `null` 触发 Pydantic 校验异常。异常被任务外层捕获并仅记录日志，文档没有进入错误状态，因此持续保持 `waiting`。

## 已确认决策

1. `summary_prompt=null` 时，运行时使用系统默认 Prompt，不改变摘要索引行为。
2. 发布版知识库 Pipeline 的所有执行异常都将关联文档标记为 `error`。
3. 异常文档不自动重试，由用户手动重试。
4. 文档保存简化后的错误信息，完整 traceback 仅写入 Worker 日志。
5. 历史空 Prompt 只做运行时兼容，不回写 Workflow 或知识库配置。
6. 当前已确认卡住的文档只做一次范围限定的 `waiting` → `error` 修复，再由用户手动重试。

## 范围

### 包含

- 兼容 `enable=true` 且 `summary_prompt` 为空或缺失的知识库节点配置。
- 覆盖 priority 和普通发布版知识库 Pipeline 任务的异常状态回写。
- 按租户、知识库、Pipeline、文档 ID 校验关联关系。
- 增加配置兼容和异常状态回写的回归测试。
- 为当前目标文档提供一次性、可核对的状态修复步骤。

### 不包含

- 前端配置页面改动。
- Workflow 或知识库配置数据迁移。
- 全量扫描和批量修改历史 `waiting` 文档。
- 普通 Workflow 执行的错误状态语义变更。
- 自动重试策略。

## 业务边界

- 只有 `PUBLISHED_PIPELINE` 且能通过租户、知识库、Pipeline 和文档 ID 四项关系校验时，才允许回写文档状态。
- 失败回写不能把已完成文档降级为错误状态，需保留合理的状态保护。
- 重试前应由页面将错误文档重新提交，避免任务层自动重复写入分片、向量或图索引。

## 验收标准

1. `summary_prompt` 为 `null` 或缺失时，知识索引节点能够完成数据校验并继续使用既有默认 Prompt。
2. 有效 Prompt 配置的行为不改变。
3. 发布版知识库 Pipeline 在节点创建、解析、模型调用、向量/图索引等异常路径下，关联文档最终为 `error`。
4. 文档错误字段不包含完整 Python traceback；Worker 日志仍包含完整异常。
5. 普通 Workflow 执行不产生文档状态回写。
6. 当前卡住文档可在状态修复后通过既有手动重试路径重新提交。

## 生产证据

- 知识库：`ed1fdc24-5e5c-4afd-92b4-28685823ac11`
- 文档：`fa8019e7-8fb5-4908-9e21-ec34f501a7c5`
- Workflow 节点：`1752477924228`
- 生产状态：`indexing_status=waiting`，`error=NULL`
- Worker 根因：`summary_index_setting.summary_prompt` 为 `None`，Pydantic 校验失败。

## 新增生产取证

2026-07-22 Worker 日志显示，`qwen3.6-35b-a3b-mtp` 因近期失败进入 cooldown 并返回 HTTP 503；该日志对应数据集 `d6bcac3f-9442-463e-9e10-fd18720fb273`，不能直接归因于目标知识库。QA 索引处理器原先吞掉模型异常并让任务记录成功，现已改为在线程汇合后重新抛出异常，交由发布版知识库 Pipeline 的文档 `error` 状态回写逻辑处理。
