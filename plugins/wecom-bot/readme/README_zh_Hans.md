

## 配置说明

1. 在企业微信中创建智能机器人，并获取 `bot_id` 和 `secret`。
2. 在 Dify 中添加 `企业微信 Bot` 插件配置。
3. 填写 `bot_id` 和 `secret`，选择用于回复消息的 Dify Chat App。
4. 设置 `WECOM_LONG_LINK_ENABLED=true`，启用 Dify worker 中的长链接进程。

Dify worker 会通过安全 WebSocket 主动连接企业微信。本版本不需要 HTTP 回调 URL、Token 或 Encoding-AESKey。
