# 独立 WeCom Worker 实现记录

## 已实现

- `api/docker/entrypoint.sh` 新增 `MODE=wecom_long_link`，直接执行 `python -m core.wecom_long_link.worker`，不启动 Celery supervisor；WeCom worker 模块的 app factory 导入延后到 `run()`，降低非运行时导入副作用。
- `docker/deploy.sh` 新增 `wecom_worker` service：复用 API 镜像、env、storage、网络和现有依赖，设置 `WECOM_LONG_LINK_ENABLED=true`、`MIGRATION_ENABLED=false`、`restart: always`，并添加基于 `/proc` 的进程健康检查。
- 通用 `worker` 显式设置 `WECOM_LONG_LINK_ENABLED=false`；`worker_graph` 保持关闭；切换拓扑不允许同一 Bot 并行持有旧/新长连接。
- 保留并接入 App/LLM 流失败终止帧：同一 `stream_id` 发送固定安全错误文案并以 `finish=true` 结束；成功发送后保持 WebSocket 继续监听下一条消息；取消、真实断流和发送失败不补伪造终止帧。
- App Router 提取下游 error event 的 code/status/message，仅写入内部日志并保留异常详情；企业微信只收到固定安全文案。
- `GUNICORN_TIMEOUT` 默认部署值统一为 900 秒。

## 验证

通过：

```text
tests/unit_tests/core/test_wecom_long_link_deployment.py: 2 passed
feedback/runtime/protocol 回归测试: 26 passed
Worker 直接异步回归 harness: 正常 final、App failure terminal frame、timer、发送失败、callback cancel 全部通过
py_compile: 通过
Ruff: 通过
bash -n api/docker/entrypoint.sh: 通过
bash -n docker/deploy.sh: 通过
diff --check: 通过
```

当前本地 pytest 环境未安装 `pytest-asyncio`，因此 worker async 测试无法由 pytest 插件执行；使用同一测试夹具的直接 `asyncio.run` harness 完成等价验证，未安装新依赖。

## 未执行

- 未部署、未提交、未推送。
- 未新增迁移；状态、租约、租户配置刷新仍复用现有实现。
- 未设置硬编码 CPU/内存限制。
