# 知识库 Q&A 分段预览为空修复

## 阶段状态

- `stage_status`: complete
- `current_skill`: code-review
- `next_skill`: git-delivery
- `resume_from`: 若用户授权，精确暂存本轮 Q&A 解析器与回归测试文件后，再执行本地提交；未授权 push、部署或重试目标线上文档。
- `continuation_mode`: authorization
- `handoff_summary`: `process_rule.rules.qa_generation.max_tokens` 已有预览、创建与异步索引的透传测试，本轮补充模型返回无编号 `Q:/A:` 时的解析兼容；编号问答答案内出现无编号示例时保持答案完整，避免预览或正式索引内容被静默截断。
- `evidence`: 定向 pytest：82 passed（`test_llm_generator.py`、`test_qa_index_processor.py`）；Ruff format/check 与本轮文件的 `git diff --check` 通过。测试以 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` 和 `-o addopts=''` 执行；仍有项目配置 `env` 未识别和 Python `cgi` 弃用警告。完整后端单元测试未形成有效结果：默认 pytest 在本机因 `pytest-cov` 导入 `coverage.data` 失败；禁用覆盖率后 xdist 报 0 items，串行 collect-only 触发 Windows access violation。
- `confirmed_at`: 2026-07-23

## 2026-07-22 新需求：Q&A 生成预算页面配置

### 已确认

- 在创建知识库页面选择“使用 Q&A 分段”时，Q&A 生成模型的 `max_tokens` 需要由页面提供配置入口。
- 默认值必须保持为 `2000`，不能继续将 `4096` 作为全局默认值。
- 该字段是 Q&A 生成模型的输出 token 预算，不等同于既有 `process_rule.rules.segmentation.max_tokens`（文档文本分段长度）。

### 已确认的契约决策

1. 配置范围：预览请求和随后提交的正式 Q&A 索引必须使用同一值。
2. 显示范围：仅当用户选择“使用 Q&A 分段”时显示该配置。
3. 交互与取值约束：复用现有 max_tokens 数字输入的交互；即最小值 `1`、最大值为 `NEXT_PUBLIC_INDEXING_MAX_SEGMENTATION_TOKENS_LENGTH`，并使用现有数字输入的增减控制。默认值为 `2000`。

### 已实现的交互决策

- 已确认：用户从 Q&A 分段切换到其他分段模式后再切回时，保留本次编辑的 Q&A 生成预算。
- 已确认：点击 Q&A 分段卡片中的“重置”时，将 Q&A 生成预算恢复为默认 `2000`。
- 已确认：Q&A 生成预算应与现有分段 `max_tokens` 一样随文档处理规则持久化；后续编辑或重新索引必须复用该值。

### 已知实现边界

- 现有预览请求为 `POST /datasets/indexing-estimate`，正式创建文档请求为独立链路；仅为预览增加参数会导致预览与正式索引使用不同预算。
- `LLMGenerator.generate_qa_document(...)` 的默认 `max_tokens` 已恢复为 `2000`，并已由定向测试覆盖默认值与页面自定义值的传递。

- `failure_evidence`: 当前 HTTP 预览接口与正式文档创建接口为两条不同链路；复用文本分段的 `process_rule.rules.segmentation.max_tokens` 会混淆字段语义。
- `required_action`: 规格已作为本地正式状态源完成更新；等待本地提交与后续代码评审。
## 原计划基线

- 原始 Q&A 分段功能提交：`cf93d8d6e2`（Q&A format segmentation support）。
- 当前前端约定：选择 `ChunkingMode.qa` 后展示接口返回的 `qa_preview`。
- 当前后端约定：默认 LLM 生成 Q&A 文本，解析为问题和答案后返回 `qa_preview`。
- 已检索 `docs/engineering/specs`、`docs/engineering/plans` 及相关 Git 历史，未找到该功能更早的独立需求文档；以上代码契约和用户确认的预览预期构成临时业务基线。

## 症状偏差与证据链

用户在 `http://10.1.14.177:3000/datasets/create` 上传 `Neo群咨询知识库.md` 后选择“使用 Q&A 分段”，预览区为空。

1. 输入文件为有效 UTF-8 Markdown，包含 340 个三级标题问题与 340 个“原因/解答”答案。
2. 服务端实时日志记录两次 `POST /console/api/datasets/indexing-estimate`，均为 HTTP 200，耗时约 20 秒，说明预览请求到达后端并执行了 LLM 路径。
3. 用户从浏览器 Network 确认真实响应为：`total_segments: 0`、`preview: []`、`qa_preview: []`。
4. 当前解析器仅匹配半角无 Markdown 包裹的 `Q数字:` 与 `A数字:`；不匹配时返回空数组且不报错。
5. 当前前端仅在 `estimate.qa_preview` 有元素时渲染问答；空数组和 mutation 失败均没有可解释的界面状态。

## 已排名假设

1. 默认 LLM 使用 Markdown 包裹、全角冒号或中文标签输出 Q&A，严格正则无法解析，导致空数组。
2. 默认 LLM 返回空文本或拒答，现有实现没有转化为可见错误。
3. 预览仅处理首个源文本分块，首块内容使 LLM 无法生成问答。
4. 前端缺少空态和失败态，放大并掩盖后端异常结果。

## 修复边界

### 包含

- 为现有 Q&A 解析器增加对常见 Markdown 包裹和中英文全角/半角分隔符的兼容，且保留严格问题/答案成对校验。
- 为 Q&A 预览空数组增加明确空态，避免将业务结果误呈现为无内容。
- 增加后端解析与前端空态的定向回归测试。

### 不包含

- 不读取或输出模型密钥、认证 Cookie、数据库密码、完整业务文档或完整 LLM 输出。
- 不修改远端数据库、模型配置或部署以外的容器；本次已按用户授权部署 API、Worker、Beat 与 Web。
- 不改变正式索引阶段的分块数量、模型选择、Prompt 内容或数据格式。

## 验收标准

1. Markdown 包裹和全角标点的标准 Q&A 输出能够解析为对应的 `question` / `answer` 对。
2. 既有半角 `Q1:` / `A1:` 格式仍能解析。
3. 不完整、没有答案或没有问题的条目不会产生无效问答。
4. `qa_preview` 为空时，页面显示明确的空预览说明而不是纯空白。
5. 定向后端和前端测试通过；已部署至目标环境，待用户以原文件在页面完成真实业务验收。

## 2026-07-23 补充：无编号 Q&A 输出兼容

### 兼容性决策

- 为避免生成模型未遵循提示词中的编号格式时直接产生空预览，解析器同时支持带编号的 `Q1:/A1:` 与无编号的 `Q:/A:`；两种格式均支持 Markdown 包裹和半角或全角冒号。
- 带编号问答仍要求问答编号一致；无编号格式仅匹配成对的 `Q:/A:` 行首标签。
- 编号问答的答案中出现无编号 `Q:/A:` 示例时，示例必须保留在原答案内，不能被识别成新的问答并截断内容。
- 本轮不扩展到 `Question:/Answer:`、列表标记或混合编号格式；这些格式需要新的运行时样本和独立契约确认。

### 证据与边界

- 现有回归测试已断言预览与异步索引均把 `process_rule.rules.qa_generation.max_tokens=1536` 传入 Q&A 生成器，生成器也以该值调用模型。
- 本轮没有连接目标环境或读取用户文档、模型密钥、Cookie 或完整模型输出；因此部署后仍需在已认证页面确认响应的 `qa_preview` 非空，且设置的最大 Tokens 与请求值一致。
