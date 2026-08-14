# MCP SSE 回退 Streamable HTTP 最终验证

## 主工作区结果

已在主工作区执行：

```bash
.venv/Scripts/python.exe -m pytest -o addopts= -q \
  tests/unit_tests/core/mcp/test_mcp_client.py \
  -k "initialize_with_mcp_url or initialize_sse_enter_failure_then_streamable_http_success_uses_streamable_transport or initialize_reuses_persisted_streamable_transport"
```

结果：`3 passed, 34 deselected`。

覆盖：

- `/mcp` 地址仍先尝试 SSE，失败才改用 Streamable HTTP；
- SSE 在上下文进入阶段失败时回退；
- 已保存为 `streamable_http` 时后续连接不调用 SSE。

已执行修改文件的 `py_compile` 与 `git diff --check`，均通过。

## 限制

隔离验证代理无法读取主工作区未提交差异，且其环境缺少 `pydantic_settings`，因此未把隔离基线结果误作本次功能验证。完整 `test_mcp_client.py` 在主工作区仍有两项既有 Flask fixture 初始化失败，与 transport 测试无关。
