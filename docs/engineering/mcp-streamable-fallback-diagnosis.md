# MCP SSE 回退 Streamable HTTP 诊断证据

## 可重复结论

GitNexus 的目标端点接受 Streamable HTTP：`POST initialize` 返回会话标识，后续 `POST tools/list` 成功。旧 SSE transport 对同一端点失败，因此服务端不可用不是根因。

现有 `MCPClient._initialize()` 以 URL 最后一个路径段推断 transport：路径末段为 `mcp` 时直接使用 Streamable HTTP；未知路径才会先使用 SSE，失败后回退 Streamable HTTP。Provider 数据模型没有用于跨请求保存成功 transport 的字段。

## 诊断反馈环

尝试执行：

```bash
pytest -q api/tests/unit_tests/core/mcp/test_mcp_client.py -q
```

当前环境在加载测试配置时缺少 `pydantic_settings`，因此不能构成可运行的回归环：

```text
ModuleNotFoundError: No module named 'pydantic_settings'
```

已执行的源码控制流检查确认：`MCPClient._initialize()` 同时包含 SSE 与 Streamable HTTP transport，并按 URL 后缀分派。后续应在该共享连接闸门建立最小单测：SSE 初始化失败、Streamable HTTP 初始化成功、会话使用成功 transport，并将成功 transport 回写到 provider。

## 影响面

- `api/core/mcp/mcp_client.py`：共享 transport 选择与回退逻辑。
- `api/tests/unit_tests/core/mcp/test_mcp_client.py`：回归测试 seam。
- `api/models/tools.py`、`api/core/entities/mcp_provider.py`：持久化 transport 所需的最小数据字段。
- `api/services/tools/mcp_tools_manage_service.py`：在远端连接成功后回写 provider 的候选落点。

## 限制

线上协议验证使用已脱敏的连接信息；本文不包含认证头、令牌、密钥或完整服务地址。
