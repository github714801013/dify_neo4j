# MCP SSE 回退 Streamable HTTP：TDD 红色测试

在 `MCPClient._initialize()` 共享连接入口新增两条待实现的回归测试。

1. URL 未显式指定 transport 时，SSE 保持默认首选；若 SSE 上下文进入阶段失败，必须按顺序尝试 Streamable HTTP，完成 session 初始化，并将成功模式记为 `streamable_http`。
2. 已保存的 `streamable_http` 模式必须使后续初始化跳过 SSE，直接使用 Streamable HTTP。

测试验证完整连接参数、调用顺序及 `ClientSession.initialize()`。生产逻辑尚未修改，因此这些断言在实现前应失败。

静态检查 `python -m py_compile api/tests/unit_tests/core/mcp/test_mcp_client.py` 和 `git diff --check` 已通过。pytest 在收集阶段因当前环境缺少 `pydantic_settings` 失败，未安装依赖。
