

## Configuration Guide

1. Create an Enterprise WeChat AI Bot and obtain its `bot_id` and `secret`.
2. In Dify, add the `WeCom Bot` plugin configuration.
3. Fill in `bot_id` and `secret`, then select the Dify Chat App that should answer messages.
4. Enable the Dify worker long-link process with `WECOM_LONG_LINK_ENABLED=true`.

The Dify worker maintains the outbound secure WebSocket connection to Enterprise WeChat. No HTTP callback URL, Token, or Encoding-AESKey is required for this version.
