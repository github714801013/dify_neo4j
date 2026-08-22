# 企业微信独立 Worker 设计决策

## 目标

将企业微信长连接从当前“通用 Celery worker 容器内由 supervisor 启动的并行子进程”进一步拆为独立 Compose 服务，隔离长连接资源与 Celery 任务故障域；同时复用已确定的长流超时与 App 失败终止帧能力。

## 已确认决策

| 决策 | 结论 |
| --- | --- |
| 交付范围 | 独立 Worker 拓扑与上一轮长流失败修复一起交付 |
| 镜像 | 复用现有 API 镜像，不新增 Dockerfile |
| 服务名 | `wecom_worker` |
| 启动模式 | 新增 `MODE=wecom_long_link`，直接执行 `python -m core.wecom_long_link.worker`，不启动 Celery/supervisor |
| 通用 worker | `WECOM_LONG_LINK_ENABLED=false` |
| Graph worker | 保持 `WECOM_LONG_LINK_ENABLED=false`，只消费 `graph_index` |
| 副本数 | 初始单副本；不以多副本 lease 作为高可用方案 |
| 租约/状态 | 复用现有 Redis message state、processing state 和 lease key，不新增 schema/key namespace |
| 租户拓扑 | 一个服务承载全部租户，复用 `_tenant_ids()` 与 `asyncio.gather` |
| 配置 | 复用完整运行时 env、DB/Redis/Plugin Daemon/Sandbox 依赖、网络和 storage mount |
| 迁移 | 独立 Worker 不执行数据库迁移，由现有迁移 job 负责；设置 `MIGRATION_ENABLED=false` |
| 生命周期 | Compose `restart: always`；单租户异常由现有 reconciler 重连，不主动退出整个服务 |
| 健康检查 | 进程级检查 `python -m core.wecom_long_link.worker`，辅以订阅/断流结构化日志；暂不新增 HTTP 端口 |
| 切换 | 原子切换：通用 worker 关闭 WeCom，新服务开启；不允许同一 Bot 并行持有两条长连接 |
| App 失败 | App/LLM 失败后发送同一 stream 的固定安全终止帧，保持 WebSocket 继续接收下一条消息 |
| 回滚 | 回滚 Compose/镜像版本，同时恢复通用 worker 的 WeCom 开关并停止独立服务，不并行运行 |
| 验收 | Compose 渲染、进程拓扑、SIGTERM、配置刷新、正常回复、App 失败终止帧、Graph worker 隔离全部验证 |

## 建议拓扑

```text
api                  MODE=api
worker               MODE=worker, WECOM_LONG_LINK_ENABLED=false
worker_graph         MODE=worker, WECOM_LONG_LINK_ENABLED=false, queue=graph_index
wecom_worker         MODE=wecom_long_link, WECOM_LONG_LINK_ENABLED=true
plugin_daemon        shared dependency
redis/db/sandbox     shared dependencies
```

`wecom_worker` 使用与现有 worker 相同的 env_file、网络、storage 和依赖条件，但不经过 `MODE=worker` 的 supervisor 分支。

## 术语

- **独立 Worker**：独立 Compose service/container 和独立主进程，不等同于当前 supervisor 在同一容器中拆出的子进程。
- **连接所有权**：同一 Bot 在一个部署中只由 `wecom_worker` 持有；通用 worker 与 graph worker 不持有。
- **App 失败**：Dify App/Plugin 流返回错误或异常；不是 WebSocket 断流、用户取消或企业微信发送失败。
- **终止帧**：同一 `stream_id`、`finish=true` 的企业微信 stream 帧；App 失败使用固定安全文案，原始错误只写内部日志。

## 证据边界

GitNexus 未提供当前 Dify repo；未指定 repo 的结果来自其他仓库，已丢弃。当前设计事实来自受限本地检索，来源标记为 `local_search` / `non_gitnexus`，不能替代完整调用图或跨项目影响分析。关键本地事实记录于：

- `api/docker/entrypoint.sh:65-72`
- `api/core/wecom_long_link/supervisor.py:42-46`
- `api/core/wecom_long_link/worker.py:280-289`
- `docker/deploy.sh:312-350`

## 不在本次范围

- 不为每个租户创建独立容器。
- 不新增独立 Redis/数据库或租约算法。
- 不立即设置硬编码 CPU/内存限制。
- 不通过灰度并行启动旧通用长连接和新独立长连接。
- 不新增 HTTP health endpoint。
