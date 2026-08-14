# MCP SSE 回退 Streamable HTTP 代码审查

## Standards

- 已执行 `py_compile` 与 `git diff --check`，均通过。
- 未发现本次改动中硬编码凭据、令牌或新增网络输入绕过。
- transport 使用 `MCPTransport` 枚举，持久化值限定为 `sse` 和 `streamable_http`；未将连接状态混入 OAuth credentials。
- 第二轮独立审查发现的阻断项已修复：`/mcp` 也保持 SSE 优先、授权链路读写 transport、`get_tools()` 传递 transport、URL 更新连接结果写入 transport。

独立 Standards/Spec 审查代理由于隔离工作树无法读取主工作区未提交差异而无法形成新的可信结论；没有将其误报为通过。

## Spec

需求已覆盖：

1. 默认连接仍先尝试 SSE。
2. SSE 连接或初始化失败后清理失败资源，再尝试 Streamable HTTP。
3. 成功连接选择的 transport 被写入 `tool_mcp_providers.transport`。
4. 工具刷新、创建、URL 更新、授权和运行时工具调用读取或更新该 transport；`streamable_http` 已保存时直接连接该 transport。
5. 迁移对既有 provider 使用安全默认 `sse`。

## 验证

MCP transport 定向测试：`3 passed, 34 deselected`。

完整 MCP 客户端测试仍有两个既有 Flask fixture 初始化失败；本次新增测试和 transport 相关测试通过。
