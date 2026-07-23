# 知识库 Pipeline local_file 文档提取为空修复

## 阶段状态

- `stage_status`: completed_with_business_acceptance_pending
- `current_skill`: 部署验证
- `next_skill`: 等待线上文档重试授权
- `resume_from`: 部署已完成。若用户明确授权线上重试，则仅重试指定文档并验证分段数、真实原文片段及图片引用。
- `continuation_mode`: manual_authorization_required
- `handoff_summary`: 用户已授权并完成仅 API、Worker、Worker Beat 的隔离部署；未提交、未推送、未重试线上文档、未修改远端数据库。Web 未重建或重启，仍运行既有镜像。
- `evidence`: 根因链路：`api/services/rag_pipeline/rag_pipeline_transform_service.py:329-346` 把 `upload_file` 转为 `local_file` 并写入 `real_file_id`；修复：`api/core/indexing_runner.py:389-470`；测试：`api/tests/unit_tests/core/rag/indexing/test_indexing_runner.py:346-408`。
- `confirmed_at`: 2026-07-23
- `implementation_evidence`: 最终单测（pytest 临时清空 addopts）运行 api/tests/unit_tests/core/rag/indexing/test_indexing_runner.py：39 passed，1 个既有 cgi 弃用警告；Ruff 检查 core/indexing_runner.py 与对应测试：All checks passed；compileall 与目标文件 git diff --check 均通过。
- `review_findings`: Spec 对照无偏差。Standards 审查的 P1 已修复：LOCAL_FILE 的 UploadFile 查询同时限制 `UploadFile.id` 与 `UploadFile.tenant_id == dataset_document.tenant_id`，并由回归测试覆盖；保持既有 `ValueError` 错误语义，不引入超出范围的新异常类型。
- `tenant_test_evidence`: `-k scopes_upload_file_to_document_tenant` 修复前：1 failed（查询参数未包含文档 tenant_id）；修复后 `-k local_file`：4 passed，35 deselected，1 个既有 `cgi` 弃用警告。

## 部署与运行时验证

- 部署时间：2026-07-23（Asia/Shanghai）。
- 使用由本地 `HEAD` 创建的临时 Git worktree，且仅覆盖 `api/core/indexing_runner.py`；构建上下文未包含开发工作区中的无关改动。
- 已构建并部署镜像：`dify-origin-api:20260723-local-file-extract-852c8c8d`。API、Worker、Worker Beat 均已切换到该镜像；远端 `/health` 返回 `status: ok`，API 容器健康检查为 `healthy`。
- 已在运行中的 API 容器核对存在 `case DataSourceType.LOCAL_FILE` 以及租户限制 `UploadFile.tenant_id == dataset_document.tenant_id`。
- Web 保持镜像 `dify-origin-web:20260723-qa-generation-unlimited`，容器启动时间早于本次服务重建；本次 Compose 命令仅指定 API、Worker、Worker Beat。
- 回滚入口：远端临时 Compose override 可将 API、Worker、Worker Beat 的镜像恢复为 `dify-origin-api:20260723-qa-generation-unlimited`，再执行同一服务范围的 `docker compose up -d --no-deps`。
- 未执行线上文档重试，因此尚未确认指定文档的真实分段与图片引用；该业务验收须等待用户明确授权。

## 原计划基线

- Pipeline 转换前，知识库上传文件使用 `DataSourceType.UPLOAD_FILE` 与 `data_source_info.upload_file_id`。
- Pipeline 转换后，文档应继续从同一 `UploadFile` 提取原始文本和图片资产；仅数据源展示/运行模型变更为 `DataSourceType.LOCAL_FILE`。
- `IndexingRunner._extract()` 应为受支持的数据源构造正确的 `ExtractSetting`，然后调用既有文件提取器。

## 症状偏差与根因

- 用户报告数据集 `ed1fdc24-5e5c-4afd-92b4-28685823ac11` 的文档 `c1da504f-8cc0-4993-9e96-b5f0db258e2e` 处理完成后，分段只出现模型兜底话术，缺少原始文档文本和图片。
- `RagPipelineTransformService._deal_document_data()` 会把 `upload_file` 改成 `local_file`，并写入 `real_file_id`。
- `IndexingRunner._extract()` 只处理 `upload_file`、Notion 和网站数据源；`local_file` 落入默认分支并返回空列表。
- 空列表仍进入后续 transform/load，`_load()` 无条件写入 `IndexingStatus.COMPLETED`，因此状态不能证明真实内容已被提取。

## 修复范围

### 包含

- 为 `DataSourceType.LOCAL_FILE` 读取 `data_source_info.real_file_id`，加载对应 `UploadFile`，复用现有 `DatasourceType.FILE` 提取器。
- `real_file_id` 缺失或对应文件不存在时抛出明确错误，交由既有索引错误处理更新文档状态，避免静默完成。
- 增加定向单元测试，覆盖成功提取、缺失文件 ID、文件记录不存在，以及既有上传文件提取不回归。

### 不包含

- 修改 Q&A Prompt、Q&A 解析格式、Q&A token 预算、文本分段规则或向量写入策略。
- 批量修复历史文档、修改远端数据库，或在未取得明确授权时重试任何线上文档。
- 新增独立图片解析器；图片继续由既有文件提取链路处理。

## 验收标准

1. `local_file` 文档携带有效 `real_file_id` 时，`IndexingRunner._extract()` 调用文件提取器并返回真实文档内容。
2. 缺失 `real_file_id` 或对应 `UploadFile` 不存在时，提取抛出错误而非返回空列表。
3. `upload_file` 的既有提取路径继续通过。
4. 定向 pytest 与差异检查通过；部署后另行获授权，仅重试指定文档并确认分段数、原文片段和图片引用均非空。
