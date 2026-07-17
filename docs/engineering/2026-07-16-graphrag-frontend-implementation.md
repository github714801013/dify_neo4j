# GraphRAG Dataset 设置页前端实现说明

Last Updated: 2026-07-17

## 页面与入口

- 页面：Dataset 创建页 `/datasets/create` 和 Dataset 设置页 `/datasets/[datasetId]/settings`。
- 组件：`web/app/components/datasets/graph-rag/graph-rag-settings.tsx`，由 Dataset 设置表单在索引设置区域挂载。
- 保存：复用现有 Dataset 设置页的单一“保存”按钮和编辑权限判断。
- 上游升级边界：`indexing-section.tsx` 只保留 GraphRAG 组件挂载和配置 props，不承载 GraphRAG 表单细节。
- 创建页由 Step Two 持有草稿，在首次创建文档请求中携带 GraphRAG 配置；创建成功前不单独创建 Dataset 配置记录。

## 接口契约

- `GET /console/api/datasets/{dataset_id}` 返回 `graph_rag_config`。
- `PATCH /console/api/datasets/{dataset_id}` 接受 `graph_rag_config`，保存到 `dataset_graph_configs`。
- `graph_rag_config.enabled` 与现有 `graph_rag_enabled` 保持兼容，前端以嵌套配置为主。
- 配置字段：`query_mode`、`graph_top_k`、`graph_max_depth`、`graph_timeout_ms`、`graph_weight`、`graph_version`。
- LlamaIndex 抽图关键配置以结构化表单暴露：抽图模型提供商、模型名称、温度、每个 Segment 最大三元组数、严格 Schema 校验。
- 抽图模型提供商和模型必须复用 Dify 已配置的模型列表/ModelSelector，不允许自由填写未配置模型。
- 创建页和设置页均通过 `useModelList(ModelTypeEnum.textGeneration)` 获取当前租户已配置模型；`GraphRagSettings` 只渲染 `ModelSelector`，不再渲染自由文本 Provider/Model 输入框。

## 页面行为

- 开关初始值来自接口；默认关闭。
- 关闭开关时仍允许编辑配置，但不会改变现有索引/检索行为；全局 `GRAPH_RAG_ENABLED=false` 时显示提示并保持功能不生效。
- Schema 仍由后端以固定领域对象校验和持久化，接口和页面都不暴露原始 Schema JSON 编辑器，避免用户提交不受控图谱结构。
- 抽图模型配置保存到 `extract_model_config` JSON 列，但由结构化字段生成；字段超出约束时停止保存并显示错误。
- 创建流程中 Schema 或抽图配置校验失败时，停止创建请求，不进入文档上传、索引和后续 Step Three；已有 Dataset 设置页保存失败时保留原配置。
- 创建页在 `useDocumentCreation.validateParams` 中先校验 GraphRAG 配置；失败时只提示错误并返回，不调用 `createFirstDocument`/`/datasets/init`。
- 加载态沿用现有页面保存按钮；只读用户禁用开关、输入框和保存按钮。

## 验收路径

1. 进入 Dataset 创建页 Step Two，确认可以开启 GraphRAG 并从已配置 Dify 模型中选择抽图模型。
2. 修改开关、查询模式、Top K、最大深度、超时、权重和抽图限制，创建成功后进入设置页确认值保持。
3. 未配置抽图模型或输入超出范围的温度/三元组数，点击创建，确认出现错误且创建请求不发送。
4. 在设置页修改同一配置，确认仍使用相同 ModelSelector 和严格校验。
