# MCP SSE 回退 Streamable HTTP 实现

## 行为

- 新建或未探测的 provider 默认使用 SSE。
- SSE 建连或初始化失败时，关闭失败连接资源后改用 Streamable HTTP。
- Streamable HTTP 成功后，将 `streamable_http` 写入 provider 的 `transport` 字段。
- 后续工具刷新、授权与工作流工具调用读取该字段并直接使用 Streamable HTTP，不再先尝试 SSE。
- 更新为新地址时重新从 SSE 探测，使用本次成功结果覆盖已保存的 transport。

## 数据迁移

迁移新增 `tool_mcp_providers.transport`，默认值为 `sse`，因此已有 provider 保持原有连接优先级。

## 验证

已通过：

```bash
.venv/Scripts/python.exe -m pytest -o addopts= -q \
  tests/unit_tests/core/mcp/test_mcp_client.py \
  -k "initialize_with_mcp_url or initialize_sse_enter_failure_then_streamable_http_success_uses_streamable_transport or initialize_reuses_persisted_streamable_transport"
```

结果：`3 passed, 34 deselected`。

完整 `test_mcp_client.py` 仍有两项既有 Flask fixture 初始化失败（`Flask("test")` 在当前环境触发 `StopIteration`），与本次 transport 逻辑无关。
