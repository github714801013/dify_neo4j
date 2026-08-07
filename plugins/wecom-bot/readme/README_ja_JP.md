

## 設定説明

1. WeCom（企業微信）で AI ボットを作成し、`bot_id` と `secret` を取得します。
2. Dify で `企業微信 Bot` プラグインの設定を追加します。
3. `bot_id` と `secret` を入力し、メッセージに応答する Dify Chat App を選択します。
4. `WECOM_LONG_LINK_ENABLED=true` を設定して、Dify worker の長接続プロセスを有効にします。

Dify worker は安全な WebSocket 接続を使って WeCom に接続します。このバージョンでは HTTP コールバック URL、Token、Encoding-AESKey は不要です。
