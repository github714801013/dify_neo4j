# 企业微信独立 Worker 代码调研记录

## 调研范围与降级说明

- 目标工作区：`D:/workplace/python/dify`
- 目标：将企业微信长连接从通用 worker 容器/进程中进一步拆分为独立服务。
- GitNexus 主查询尝试：
  1. 未指定 repo 的语义 query 返回了其他仓库（如 `9ji-admin`、`jiuji-mp`），结果与当前 Dify 工作区无关，已丢弃，不能作为当前仓事实。
  2. 显式 repo=`D:/workplace/python/dify` 返回 `Repository is not available through this MCP server`。
- 因当前仓库未通过 GitNexus 授权/索引，按节点规则降级为受限本地检索。以下证据均标记：`source: local_search`、`authority: non_gitnexus`。本地文本不能证明完整调用图、impact、trace 或跨项目影响范围。

## 代码事实（本地检索）

### 1. 当前 worker 容器在同一容器内由 supervisor 启动两个进程

- `api/docker/entrypoint.sh:65-72`
  - `WECOM_LONG_LINK_ENABLED=true` 时不直接启动 Celery，而是执行 `python -m core.wecom_long_link.supervisor`。
  - 否则执行 Celery worker。
- `api/core/wecom_long_link/supervisor.py:42-46`
  - supervisor 同时 `Popen(_celery_command())` 和 `Popen([sys.executable, "-m", "core.wecom_long_link.worker"])`。
- `api/core/wecom_long_link/supervisor.py:49-77`
  - supervisor 转发 SIGTERM/SIGINT，并在子进程退出后处理生命周期。

### 2. 长连接 worker 的业务入口

- `api/core/wecom_long_link/worker.py:280-289`
  - `run()` 创建 Flask app context，读取所有租户，`asyncio.gather` 启动每个租户的 `_run_tenant`。
- `api/core/wecom_long_link/worker.py:28-29`
  - `_run_tenant` 构造配置 provider。
- `api/core/wecom_long_link/worker.py:254-277`
  - 通过 `WeComConfigReconciler` 管理 client 生命周期。
- `api/core/wecom_long_link/client.py:215-227`
  - `WeComLongLinkManager` 按 provider 配置创建 `WeComOutboundClient`。

### 3. 当前部署配置只提供通用 worker 与图索引 worker

- `docker/deploy.sh:312-331`
  - `worker` 使用 `MODE=worker`，并将 `WECOM_LONG_LINK_ENABLED` 透传；当前默认任务队列由 `CELERY_WORKER_QUEUES` 指定。
- `docker/deploy.sh:333-350`
  - `worker_graph` 使用 `MODE=worker`、`WECOM_LONG_LINK_ENABLED: "false"`，仅消费 `graph_index`。
- `docker/deploy.sh:176-259`
  - 运行时环境文件统一写入数据库、Redis、插件内部 API 等配置；独立 WeCom 服务若复用该入口，必须获得同等 Flask/DB/Redis 配置。

### 4. 当前测试边界

- `api/tests/unit_tests/core/test_wecom_long_link_worker.py`
  - 覆盖 worker callback、反馈帧、下游 stream close、取消和发送失败。
- `api/tests/unit_tests/core/test_wecom_long_link_client.py`
  - 覆盖 lease、连接关闭和 handler 生命周期。
- `api/tests/unit_tests/core/test_wecom_long_link_runtime.py`
  - 覆盖 reconciler、租户/配置和路由相关行为。
- 未发现当前 compose/deploy 对独立 WeCom service 的专门测试或服务级 smoke test（本地文本检索范围内）。

## 设计推断（非 GitNexus 事实）

1. 当前所谓“独立”只是在同一 worker 容器的 supervisor 中独立 Python 子进程；要实现容器级隔离，需要新增 compose service 或专用 entrypoint command。
2. 最小拆分候选是：
   - 通用 `worker`：`WECOM_LONG_LINK_ENABLED=false`，继续消费 Celery 业务队列；
   - 新增 `wecom_worker`：复用 API 镜像和运行时环境，直接执行 `python -m core.wecom_long_link.worker`，不启动 Celery；
   - `worker_graph` 保持长连接关闭。
3. 直接执行 `worker` 模块需要 Flask app context、DB、Redis、插件/API 内部配置；不能只复制一个容器而遗漏 `env_file`、依赖服务、网络和停止信号处理。
4. 是否保留 supervisor、是否让专用 service 使用独立资源限制/副本数、以及多副本 Bot lease 语义，需在规格阶段确认；本地文本不能证明外部部署的完整消费者或多实例关系。

## 未确认项与风险

- `remote_gitnexus` 未提供当前 Dify repo，因此无法确认完整调用图、所有部署入口或跨项目消费者。
- 当前工作区包含大量无关未提交改动；后续实现必须按文件精确暂存，不能使用 `git add .`。
- 专用 worker 的副本数必须默认 1 或明确 lease/多实例策略，否则可能重新引入 Bot 连接竞争。
- 新增 compose service 会改变部署拓扑、资源占用、日志与升级回滚流程；应先在规格阶段确定健康检查、depends_on、restart 和 graceful shutdown。

## 调研结论

当前代码已经把 WeCom 进程与 Celery 进程分开，但仍共享一个容器和运行时资源。若目标是进一步隔离资源与故障域，推荐新增专用 `wecom_worker` service，并让通用 worker 显式关闭长连接；不应把本地文本推断扩大为“已证明所有影响范围”。
