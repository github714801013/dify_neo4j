# 企业微信长连接拆分独立 Worker 规格

- 任务 ID：`wecom-dedicated-worker`
- 日期：2026-08-22
- 阶段：`dev-spec-gen / to-spec`
- 期望修订：`10`
- 状态：正式实现规格（本阶段只产出规格，不提交、不部署、不执行节点提交）
- 决策来源：`docs/engineering/plans/wecom-dedicated-worker-design.md` 中已确认的推荐决策。

## Problem Statement

当前企业微信长连接虽然已经由独立 Python 子进程承载，但该子进程仍与 Celery worker 位于同一个通用 worker 容器，并由同一个 supervisor 管理。长连接的连接、租约、配置刷新和 App 流处理因此与 Celery 任务共享容器故障域、重启边界、日志和运维操作边界。

这种拓扑不能提供真正的容器级隔离：长连接异常可能触发通用 worker 的生命周期处理；通用 worker 的变更或资源压力也可能影响企业微信连接。与此同时，切换期间若旧通用 worker 和新服务同时持有同一 Bot，可能产生重复订阅、消息竞争或状态竞态。

本需求还必须保留上一轮已确认的长流失败语义：App/LLM 失败时向企业微信发送同一 `stream_id` 的固定安全终止帧；取消、断流和发送失败时不补偿发送；长流默认超时保持 900 秒；原始错误只进入内部日志。

## Solution

新增与 API 使用同一镜像的 Compose service `wecom_worker`，使用明确的 `MODE=wecom_long_link`，直接运行 `python -m core.wecom_long_link.worker`。该服务不启动 Celery，也不经过 supervisor；初始固定为单副本，承载全部租户的企业微信长连接。

通用 `worker` 显式设置 `WECOM_LONG_LINK_ENABLED=false`，只承担既有 Celery 任务；`worker_graph` 同样关闭长连接且只消费 `graph_index` 队列。`wecom_worker` 复用现有完整运行时环境、DB、Redis、Plugin Daemon、Sandbox、storage mount、网络以及企业微信已有的消息状态、processing 状态和 lease 状态，不新增数据库 schema 或 Redis key namespace。

服务以 30 秒为配置刷新周期，通过现有租户枚举和 reconciler 管理连接；单租户异常只影响该租户并由现有 reconciler 重连，不使整个服务退出。Compose 使用 `restart: always`，以进程级健康检查确认目标 worker 主进程存活，不新增 HTTP health endpoint。

切换必须是原子切换：先关闭通用 worker 的企业微信长连接并确认旧连接退出，再启动独立服务；任何阶段都不允许同一 Bot 由旧、新两个长连接并行持有。回滚通过 Compose/镜像版本切换完成，停止独立服务后恢复通用 worker 的长连接开关，仍不并行运行。

## Goals

1. 形成独立的 `wecom_worker` Compose service 和独立主进程故障域。
2. 让通用 `worker` 和 `worker_graph` 明确不建立企业微信长连接。
3. 保持单副本承载全部租户，复用现有租户、配置、租约、消息幂等和重连机制。
4. 保持企业微信协议、`req_id` 关联、稳定 `stream_id`、正常流式终止帧和消息状态语义不变。
5. 保持 App/LLM 失败的固定安全终止帧、取消/断流/发送失败不补帧，以及 900 秒长流默认超时。
6. 通过进程级健康检查、SIGTERM 正常退出、30 秒配置刷新和 Compose 自动重启降低运维故障域。
7. 使部署、原子切换和回滚均可由可观察的 Compose/进程/连接状态验收。

## User Stories

1. 作为企业微信用户，我希望长连接拆分后仍能正常发送消息并获得 Dify App 回复，以便不感知部署拓扑变化。
2. 作为企业微信用户，我希望正常流式回复继续使用同一个 `stream_id`，并以唯一的 `finish=true` 帧结束，以便客户端能正确收敛消息。
3. 作为企业微信用户，我希望 App 或 LLM 失败时收到固定且安全的终止消息，以便知道本次请求结束而不会看到内部异常细节。
4. 作为企业微信用户，我希望 App 失败终止帧发送后 WebSocket 继续接收下一条消息，以便单次失败不会使 Bot 永久失联。
5. 作为企业微信用户，我不希望取消、连接断流或发送失败时收到虚假的补偿终止帧，以便消息状态与真实连接状态一致。
6. 作为租户管理员，我希望一个租户的配置变化能在最多一个刷新周期后生效，以便不必重启整个服务。
7. 作为租户管理员，我希望一个租户的连接异常不会中断其他租户的连接，以便多租户服务保持隔离。
8. 作为租户管理员，我希望配置版本变化时旧连接被停止后再建立新连接，以便同一 Bot 不会同时存在两条长连接。
9. 作为运维人员，我希望企业微信长连接拥有独立的 Compose service，以便单独查看日志、健康状态和重启结果。
10. 作为运维人员，我希望通用 worker 明确关闭企业微信长连接，以便 Celery 任务重启或扩缩容不会意外抢占 Bot 连接。
11. 作为运维人员，我希望 `worker_graph` 只消费 `graph_index`，以便图索引任务不会意外建立长连接或消费业务队列。
12. 作为运维人员，我希望独立服务只运行一个副本，以便在没有多副本高可用设计的前提下避免连接竞争。
13. 作为运维人员，我希望服务异常退出后由 Compose 自动重启，以便短暂进程故障可以自动恢复。
14. 作为运维人员，我希望发送 SIGTERM 时服务能停止刷新、关闭连接并释放租约后正常退出，以便升级和回滚不会遗留 Bot 占用。
15. 作为运维人员，我希望健康检查只反映目标 worker 进程是否存活，而不会为了检查而启动第二个长连接进程。
16. 作为运维人员，我希望独立服务不执行数据库迁移，以便迁移仍由既有迁移 job 统一控制。
17. 作为运维人员，我希望服务复用现有 env、DB、Redis、Plugin Daemon、Sandbox、storage 和网络，以便拆分不引入第二套基础设施配置。
18. 作为开发人员，我希望消息的 `PROCESSING`、`DIFY_COMPLETED` 和 `REPLY_SENT` 状态继续复用，以便已有重试和幂等行为不被破坏。
19. 作为开发人员，我希望已完成但回复发送失败的消息仍能沿用既有缓存结果重发，而不是再次调用 App，以便避免重复执行。
20. 作为安全人员，我希望 secret 不出现在日志、Redis、状态接口、任务参数或健康检查结果中，以便长连接拆分不会扩大凭据泄露面。
21. 作为发布人员，我希望切换操作先停止旧长连接再启动新服务，以便发布期间不出现同一 Bot 的并行连接。
22. 作为发布人员，我希望回滚能通过 Compose/镜像版本恢复通用 worker，并在此之前停止独立服务，以便回滚不依赖数据迁移或人工清理 key。
23. 作为测试人员，我希望能从渲染后的 Compose、进程表、WebSocket 帧、Redis 状态和日志观察验收结果，以便测试不依赖私有实现细节。
24. 作为支持人员，我希望日志保留原始 App/LLM 错误和 traceback，而对外只发送固定安全文案，以便同时满足排障和信息最小暴露。
25. 作为长流使用者，我希望默认 900 秒的长流超时继续有效，以便长时间运行的 App 不因拆分 Worker 而改变上一轮已确认的行为。

## Terminology

- **独立 Worker**：独立 Compose service、独立容器和独立主进程；不等同于同一容器内由 supervisor 启动的子进程。
- **通用 worker**：运行 Celery 业务队列的现有 `worker` 服务。
- **Graph worker**：只消费 `graph_index` 队列的 `worker_graph` 服务。
- **`wecom_worker`**：只承载企业微信长连接的专用服务。
- **连接所有权**：一个部署中同一 Bot 只允许由一个有效长连接持有；切换完成后所有权归 `wecom_worker`。
- **租户**：企业微信配置和 Dify App 所属的业务租户；独立服务在一个进程中遍历全部租户。
- **配置版本**：现有配置 provider 用于判断 endpoint 是否需要停止并重建连接的版本标识。
- **reconciler**：根据当前启用配置、配置版本和运行中的 client 进行增删替换的现有协调器。
- **`stream_id`**：一次企业微信流式回复的稳定标识；正常回复和 App 失败终止帧必须使用同一标识。
- **终止帧**：`finish=true` 的企业微信 stream 帧。App/LLM 失败的终止帧使用固定安全文案。
- **App/LLM 失败**：Dify App、Plugin 流、模型调用或 App 流转换返回错误/异常；不包括 WebSocket 断流、取消和企业微信发送失败。
- **取消/断流**：当前消息处理被取消、WebSocket 已关闭或连接/租约终止导致 handler 结束的路径。
- **消息状态**：现有 `PROCESSING`、`DIFY_COMPLETED`、`REPLY_SENT` 状态及其 Redis 存储语义。
- **原子切换**：旧长连接完全停止并释放连接/租约后，才允许新服务建立长连接的切换过程。

## Current State and Evidence Boundary

### 当前拓扑

```text
worker (MODE=worker, WECOM_LONG_LINK_ENABLED=true)
└── supervisor
    ├── Celery worker
    └── 企业微信长连接 worker

worker_graph (MODE=worker, WECOM_LONG_LINK_ENABLED=false, queue=graph_index)
```

当前的“独立”是同一容器内的进程隔离，不是容器级 service 隔离。

### 目标拓扑

```text
api                  MODE=api
worker               MODE=worker, WECOM_LONG_LINK_ENABLED=false
worker_graph         MODE=worker, WECOM_LONG_LINK_ENABLED=false, queue=graph_index
wecom_worker         MODE=wecom_long_link, WECOM_LONG_LINK_ENABLED=true
                     └── python -m core.wecom_long_link.worker
plugin_daemon        共享依赖
redis/db/sandbox     共享依赖
```

### 受限代码事实

以下代码事实均来自受限本地文本检索，标记为 `source: local_search`、`authority: non_gitnexus`：

- `api/docker/entrypoint.sh:65-72`：当 `WECOM_LONG_LINK_ENABLED=true` 时，现有 worker 入口执行 `core.wecom_long_link.supervisor`；否则执行 Celery worker。
- `api/core/wecom_long_link/supervisor.py:42-46`：现有 supervisor 同时启动 Celery 子进程和企业微信长连接子进程，并在 `:49-75` 处理信号和子进程收尾。
- `api/core/wecom_long_link/worker.py:23-29, 262-289`：worker 在 Flask app context 中枚举租户，以每租户任务和 `asyncio.gather` 运行，并通过 reconciler 周期性刷新配置。
- `api/core/wecom_long_link/reconciler.py:25-42`：配置缺失或版本变化时取消旧 client，随后按新配置建立 client；单个运行任务结束后从运行表移除。
- `docker/deploy.sh:312-352`：现有 `worker` 与 `worker_graph` 复用 API 镜像、env file、storage、网络以及 init permissions、Sandbox、Plugin Daemon 依赖；`worker_graph` 使用 `graph_index` 队列并关闭长连接。
- `api/core/wecom_long_link/state.py:39-63`：lease 和 message key 使用既有 `wecom:longlink` 命名结构；消息状态协议包含 `PROCESSING`、`DIFY_COMPLETED`、`REPLY_SENT`。
- `api/core/wecom_long_link/store.py:83-127`：Redis 状态存储提供 lease 获取/续租/释放、消息 claim、完成、回复已发送和 processing 重置能力。
- `api/core/wecom_long_link/protocol.py:99-116`：企业微信 stream 回复携带原始请求的 `req_id`、稳定的 stream id 和 `finish` 字段；文本回复使用 `finish=true`。
- `api/core/wecom_long_link/app_router.py:17-28, 127-158` 与 `worker.py:193-235`：App 流错误有固定安全文案和内部异常详情记录；worker 对 App 失败、取消和普通异常分别处理。
- `api/core/wecom_long_link/client.py:156-207`：消息 claim、已有完成结果重发、回复已发送跳过、处理失败重置 processing 以及 lease/连接释放均已有状态路径。

GitNexus 未提供当前 Dify repo。未指定 repo 的查询命中了其他仓库，已丢弃；显式指定当前工作区也不可用。因此上述本地证据不能证明完整调用图、`impact`、`trace`、所有消费者或跨项目影响范围；本规格不把 GitNexus 失败结果伪装成调用图。设计结论和本规格中的目标契约以已确认设计决策为准，而不是将本地检索推断扩大为完整影响分析。

## Target Architecture and Topology

### 服务职责

| 服务 | 启动模式 | 企业微信长连接 | 队列/职责 | 副本 |
| --- | --- | --- | --- | --- |
| `worker` | `MODE=worker` | `WECOM_LONG_LINK_ENABLED=false` | 既有 Celery 业务队列 | 沿用现有部署 |
| `worker_graph` | `MODE=worker` | `WECOM_LONG_LINK_ENABLED=false` | 只消费 `graph_index` | 沿用现有部署 |
| `wecom_worker` | `MODE=wecom_long_link` | `WECOM_LONG_LINK_ENABLED=true` | 只运行企业微信长连接主进程 | 初始单副本 |

`wecom_worker` 不启动 Celery、不启动 supervisor、不消费 Celery 队列、不开放新的对外端口。它只负责从现有配置入口读取启用的企业微信 endpoint，建立和维护长连接，接收消息并调用现有 Dify App 流程。

### 多租户拓扑

独立服务启动一个 Flask app context，读取全部租户，并为每个租户运行一个现有 reconciler 任务。一个服务副本承载全部租户；不按租户创建容器，不以增加副本数作为本需求的高可用方案。

租户之间的连接、配置刷新和异常处理必须相互隔离：单租户 reconciler 或 client 的异常进入该租户已有的重连路径，不主动结束整个 `wecom_worker` 进程。

## Configuration and Startup Contract

### 必须满足的外部契约

1. 新增 Compose service 名称固定为 `wecom_worker`。
2. `wecom_worker` 使用与现有 API/worker 相同的 API 镜像和镜像版本。
3. `wecom_worker` 明确设置 `MODE=wecom_long_link` 和 `WECOM_LONG_LINK_ENABLED=true`。
4. 该模式直接执行 `python -m core.wecom_long_link.worker`，不得启动 Celery 或 supervisor。
5. 通用 `worker` 明确设置 `WECOM_LONG_LINK_ENABLED=false`。
6. `worker_graph` 明确设置 `WECOM_LONG_LINK_ENABLED=false`，并且 `CELERY_WORKER_QUEUES` 只有 `graph_index`。
7. `wecom_worker` 明确设置 `MIGRATION_ENABLED=false`；数据库迁移只由现有 migration job 执行。
8. `wecom_worker` 的 Compose restart 策略为 `always`。
9. `wecom_worker` 初始副本数为 1；服务配置不得默认产生多个连接持有者。
10. 长流超时默认值保持 900 秒；不得因拆分服务引入另一套冲突的默认长流超时。
11. 配置刷新周期为 30 秒；配置新增、删除、禁用或版本变化应在刷新周期内进入 reconciler 处理。
12. 未配置企业微信 endpoint 时，进程仍可保持健康；不能因为“当前无租户连接”而伪造成功连接或启动第二个进程。

### 启动与停止

- 启动时只创建一个企业微信长连接主进程，建立 app context 后读取全部租户配置。
- `MODE=worker` 的既有 Celery 启动契约保持不变，但长连接开关必须为 false。
- `MODE=wecom_long_link` 不得落入通用 worker 的 supervisor 分支。
- 收到 SIGTERM 后停止配置刷新和新消息处理，关闭各租户连接，释放 lease，并在 Compose 的正常停止窗口内退出。
- SIGTERM 期间不得为了“补齐结果”发送 App 失败终止帧；取消、断流和发送失败统一遵守不补帧语义。
- 意外退出由 `restart: always` 负责拉起；单租户异常不应依赖进程重启恢复。

### 健康检查

健康检查必须是进程级检查，目标是确认 `wecom_worker` 的既有主进程存活，不新增 HTTP 端口，不通过再次执行 worker 模块来启动第二个长连接进程，也不把 secret 放进检查参数或输出。

健康检查通过不能等同于所有 Bot 都已订阅成功；订阅、断流、重连和租约情况通过既有结构化日志及后续运维观测确认。具体检查工具、时间间隔、重试次数和停止宽限期列入未确认项，在实现阶段锁定。

## Dependencies, Network, and Mounts

`wecom_worker` 复用现有 worker 的运行时契约，不复制一套基础设施：

- 复用现有 API 镜像、完整 `env_file` 和插件内部 API 配置。
- 复用 DB、Redis、Plugin Daemon、Sandbox，以及现有 app context 所需的运行时环境。
- 复用现有 storage mount，保持读写路径和权限约束不变。
- 复用现有 `default` 与 `ssrf_proxy_network` 网络。
- 复用现有 init permissions、Sandbox、Plugin Daemon 的依赖条件。
- 不新增独立 DB、Redis、Plugin Daemon、Sandbox、storage、网络或对外端口。
- 不执行 migration；`MIGRATION_ENABLED=false` 是独立服务的硬约束。

具体 Compose 生成入口和现有部署文件之间的同步方式尚未确认，不能在本规格中假定某个生成文件就是唯一事实源；但所有最终渲染结果必须满足上述 service contract。

## Lease and Multi-Tenant Semantics

1. `wecom_worker` 以单副本承载所有租户；通用 `worker` 和 `worker_graph` 不建立企业微信连接。
2. 连接 lease、message state、processing state 使用现有 Redis 存储、现有 key 组成和现有原子操作；不得新增 schema 或 key namespace。
3. lease 仍用于防止同一租户/实例/Bot 的竞争连接；单副本是部署约束，不能替代 lease。
4. 连接只有在取得 lease、订阅成功并进入既有 client 生命周期后才视为有效；租约续租失败时按现有 client 终止和重连路径处理。
5. 配置 provider 仍按租户读取企业微信 endpoint；secret 只在受控运行时内存中使用，不写入日志、Redis、状态响应、Celery 参数或监控标签。
6. 配置版本变化时必须先停止旧 client 并等待其任务收尾，再建立新 client；不能通过并行连接等待 lease 自然过期来完成切换。
7. 一个租户没有可用配置、配置被禁用或配置不完整时，不影响其他租户；该租户由 reconciler 在后续刷新中重新评估。
8. 消息幂等继续使用现有 `claim`、`PROCESSING`、`DIFY_COMPLETED`、`REPLY_SENT` 语义：
   - 新消息成功 claim 后进入 `PROCESSING`。
   - App 成功且结果可复用时进入 `DIFY_COMPLETED`，回复确认发送后进入 `REPLY_SENT`。
   - 已有 `DIFY_COMPLETED` 结果沿用既有缓存结果重发，不重新调用 App。
   - 已有 `REPLY_SENT` 消息继续跳过重复处理。
   - App 失败、取消、断流或发送失败不得进入成功状态；沿用现有 processing 重置路径，并保证一次 invocation 只重置一次。

## Lifecycle, Health, and Signals

### 启动阶段

1. Compose 启动 `wecom_worker`，入口根据 `MODE=wecom_long_link` 直接执行长连接模块。
2. 模块创建 Flask app context，枚举所有租户。
3. 每个租户建立配置 provider 和 reconciler。
4. reconciler 为启用 endpoint 创建 client，client 取得 lease 后订阅企业微信。
5. 任何单租户配置或连接错误只进入该租户的既有重连路径，不使全局进程退出。

### 稳态阶段

- 每 30 秒刷新一次配置并执行 reconciler。
- 新增配置建立连接，禁用/删除配置停止连接，配置版本变化执行停止后重建。
- WebSocket 断流、心跳失败、租约失败和临时网络错误由现有 client/reconciler 重连。
- 单租户异常不得取消其他租户任务，不得触发全服务退出。
- 长流默认超时保持 900 秒；长流超时的错误分类和消息状态必须与上一轮修复保持一致。

### SIGTERM 阶段

- 停止配置刷新和接收新的业务消息。
- 使正在处理的 invocation 进入已有取消/断流收尾路径；不发送补偿帧。
- 关闭 WebSocket，停止 ping 和 lease renewal，释放连接 lease。
- 等待任务完成局部清理后正常退出；不得强杀仍由其拥有的生成器或跨线程写帧。
- 服务停止后不得留下旧进程继续持有 Bot 连接，再允许新版本启动。

### 健康与重启

进程级健康检查失败时由 Compose 按 `restart: always` 重启服务。健康检查只回答进程存活，不承诺每个租户订阅成功；租户级订阅和断流状态依靠结构化日志与重连结果观察。

## Error and Message-State Semantics

### 状态与帧矩阵

| 场景 | 企业微信帧 | WebSocket/服务行为 | 消息状态 | 日志 |
| --- | --- | --- | --- | --- |
| 正常 App 流完成 | 沿用同一 `stream_id` 发送正常中间帧，最后一帧 `finish=true` | 当前连接继续接收下一条消息 | 按既有路径进入 `DIFY_COMPLETED`、`REPLY_SENT` | 记录正常完成 |
| App/LLM 流失败 | 发送一次同一 `stream_id` 的固定安全终止帧，`finish=true` | 当前 WebSocket 继续接收下一条消息 | 不进入成功状态，沿用失败后的 processing 重置 | 原始错误、类型、状态和 traceback 只写内部日志 |
| 长流超时 | 若属于 App/LLM 失败路径，使用同一固定安全终止帧；不暴露超时原文 | handler 收尾，连接保持可继续接收 | 不标记成功，沿用失败重置 | 原始超时信息只写内部日志 |
| 用户取消/任务取消 | 不发送终止帧或补偿帧 | 停止 timer、feedback 和生产事件 | 不进入成功状态，processing 只重置一次 | 记录取消原因 |
| WebSocket 断流 | 不发送终止帧或补偿帧 | 结束当前 handler，按现有 client/reconciler 重连 | 不进入成功状态，processing 只重置一次 | 记录断流/重连原因 |
| 企业微信发送失败 | 不发送第二个补偿帧，不重复尝试发送终止帧 | 停止当前反馈发送，按既有连接/重连路径处理 | 不进入成功状态，processing 只重置一次 | 记录发送异常及上下文，不记录 secret |
| 单租户连接/租约失败 | 不产生 App 补偿帧 | 只停止并重连该租户 | 受影响消息按既有失败路径处理 | 记录租约/连接失败 |

当前固定安全文案为“服务暂时不可用，请稍后重试。错误码：`APP_STREAM_ERROR`”。实现不得将 App/LLM 原始异常替换进对外文案，也不得通过新增状态接口或 Redis 字段暴露原始错误。

### 关键不变量

1. App 失败终止帧与该 invocation 的正常帧使用同一个 `stream_id`。
2. App 失败最多产生一个固定 `finish=true` 终止帧；终止帧发送失败时不再补第二帧。
3. 取消、断流和发送失败绝不通过“补帧”伪造正常结束。
4. 迟到的事件、帧或生成器返回值不能在取消/断流后写入 `DIFY_COMPLETED` 或 `REPLY_SENT`。
5. 正常完成才允许进行成功状态迁移；失败路径只能沿用已有重置/重试语义。
6. 长流超时默认 900 秒；不得将 900 秒与 lease TTL 或其他连接超时未经确认地混为新的状态机制。
7. secret、完整凭据和原始异常不进入企业微信帧、Redis message content、健康检查结果或任务参数。

## Atomic Switch and Rollback

### 原子切换

切换配置必须将以下内容视为同一个发布变更集：

- 通用 `worker` 的 `WECOM_LONG_LINK_ENABLED` 从旧启用状态切为 false。
- `worker_graph` 保持 false 且只消费 `graph_index`。
- 新增并启用 `wecom_worker`，设置 `MODE=wecom_long_link`、`WECOM_LONG_LINK_ENABLED=true`、单副本和 `MIGRATION_ENABLED=false`。

运行顺序必须具有明确的停止屏障：

1. 先停止/重建通用 worker，使其不再持有企业微信连接。
2. 等待 supervisor/长连接子进程退出、WebSocket 关闭并释放 lease。
3. 确认旧长连接进程不存在后，启动 `wecom_worker`。
4. 验证新主进程、订阅日志和单 Bot 连接所有权。

不允许以旧服务和新服务同时运行、等待 Redis lease 冲突或等待旧连接自然超时作为切换策略。Compose 的最终渲染结果和进程观察必须证明不存在并行长连接。

### 回滚

回滚采用 Compose/镜像版本切换，不执行数据迁移：

1. 停止 `wecom_worker`，等待其连接关闭和 lease 释放。
2. 切换到上一版 Compose/镜像组合。
3. 恢复通用 worker 的企业微信开关，使旧版本通用 worker 按既有方式承载长连接。
4. 验证通用 worker 的 supervisor/长连接恢复，确认新服务未运行且同一 Bot 没有并行连接。
5. 保持现有 Redis 状态和 key 语义，不执行 namespace 清理或 schema 回滚。

如果旧版本不支持 `wecom_worker` service，回滚仍必须先停止新服务，再启动旧通用 worker；不得通过同时保留两个版本来缩短切换时间。

## Implementation Decisions

- 在部署拓扑中增加 `wecom_worker`，复用 API 镜像，不新增 Dockerfile、独立基础设施或外部端口。
- 增加 `wecom_long_link` 启动模式，使目标主进程直接执行长连接模块；该模式不启动 Celery 或 supervisor。
- 将通用 worker 的企业微信开关固定为 false，并保持 graph worker 的开关为 false、队列范围固定为 `graph_index`。
- 专用服务初始固定单副本，使用现有租户枚举和 `asyncio.gather` 承载全部租户。
- 复用现有配置 provider、reconciler、client、协议适配、App router、反馈协调器和消息状态存储，不复制第二套连接或 App 调用逻辑。
- 复用现有 Redis lease、message、processing 状态和 key 组成，不新增 schema、字段或 key namespace。
- 复用现有 DB、Redis、Plugin Daemon、Sandbox、env、storage mount、网络和依赖条件。
- 独立服务强制 `MIGRATION_ENABLED=false`，迁移职责仍归现有 migration job。
- 使用 `restart: always`、进程级健康检查、SIGTERM 正常退出和 30 秒配置刷新。
- 以旧长连接停止并释放所有权为切换前置条件；新旧连接不得并行。
- App/LLM 失败使用同一 `stream_id` 的固定安全 `finish=true` 帧；原始错误只入内部日志，WebSocket 继续接收下一条消息。
- 取消、断流和发送失败不发送补偿帧，不回写成功状态；长流默认超时保持 900 秒。
- 回滚只采用 Compose/镜像版本切换并恢复通用 worker，不新增迁移或并行运行策略。

## Testing Decisions

测试以外部可观察行为为中心，不锁定 supervisor 内部调用顺序、私有字段、线程实现或某个具体 Compose 生成函数。优先复用已有的企业微信 worker、client、reconciler、协议、反馈和部署配置测试 seam；只有现有 seam 无法观察进程拓扑或停止行为时，才增加最小的 service smoke harness。

现有 prior art（均为 `source: local_search`、`authority: non_gitnexus` 发现）：

- `api/tests/unit_tests/core/test_wecom_long_link_worker.py`：worker callback、反馈帧、下游 stream close、取消和发送失败。
- `api/tests/unit_tests/core/test_wecom_long_link_client.py`：lease、连接关闭和 handler 生命周期。
- `api/tests/unit_tests/core/test_wecom_long_link_runtime.py`：reconciler、租户/配置和路由行为。
- `api/tests/unit_tests/core/test_wecom_long_link_protocol.py` 与 `test_wecom_long_link_feedback.py`：协议帧和反馈状态行为（若当前分支已有对应测试入口，则沿用）。

推荐测试 seam：

1. **渲染后的 Compose seam**：验证服务、环境、命令、依赖、mount、网络、restart 和副本约束。
2. **入口模式 seam**：验证三种 worker 模式的实际进程拓扑，而不是只检查环境变量。
3. **reconciler/config seam**：注入租户配置变化，观察停止、重建和 30 秒刷新行为。
4. **client/state seam**：使用受控 lease/state store 观察单连接所有权、幂等状态和 reset 次数。
5. **protocol/feedback seam**：使用 fake WebSocket 观察 `req_id`、同一 `stream_id`、`finish` 和失败帧数量。
6. **signal/process seam**：以最小子进程或 service smoke 测试验证 SIGTERM、进程退出和不产生第二个 worker。

## Testing and Acceptance Matrix

| 编号 | 验收场景 | 操作/输入 | 必须观察到的结果 |
| --- | --- | --- | --- |
| A01 | Compose service 存在 | 渲染目标 Compose 配置 | 存在 `wecom_worker`，使用 API 镜像，`restart: always`，单副本约束成立 |
| A02 | 专用启动命令 | 启动 `MODE=wecom_long_link` 服务并观察进程表 | 只有 `python -m core.wecom_long_link.worker` 对应主进程；无 Celery、无 supervisor、无第二个长连接进程 |
| A03 | 通用 worker 隔离 | 启动通用 worker | `WECOM_LONG_LINK_ENABLED=false`，不创建企业微信连接 |
| A04 | Graph worker 隔离 | 启动 `worker_graph` 并检查队列/连接 | 开关为 false，只消费 `graph_index`，不创建企业微信连接 |
| A05 | 运行时依赖复用 | 检查最终 service 配置并启动 | env、DB/Redis、Plugin Daemon、Sandbox、storage、网络和既有依赖条件与 worker 契约一致；无新增基础设施 |
| A06 | 不执行迁移 | 以 `MIGRATION_ENABLED=false` 启动 | 不执行 migration；迁移仍由现有 job 负责 |
| A07 | 全租户承载 | 使用多个租户和多个 endpoint 配置启动 | 一个 service 副本为全部租户创建 reconciler；单租户失败不阻断其他租户 |
| A08 | 配置刷新 | 在刷新周期内新增、禁用、删除或修改配置版本 | 最多一个 30 秒周期后进入 reconciler；旧 client 先停止，新 client 后建立 |
| A09 | lease 排他 | 在切换或受控竞争场景中检查同一 Bot lease | 同一 Bot 同时最多一个有效连接；无 lease 不得继续订阅/发送 |
| A10 | 原子切换 | 先关闭旧通用长连接，再启动专用服务 | 旧进程、旧 WebSocket 和 lease 已释放后才出现新连接；无并行连接窗口 |
| A11 | 正常退出 | 对专用服务发送 SIGTERM | 停止刷新、关闭连接、停止 ping/续租、释放 lease，并在宽限期内正常退出 |
| A12 | 健康检查 | 执行进程级 healthcheck 并检查副作用 | 能识别目标主进程存活；不开放 HTTP 端口、不启动第二个 worker、不泄露 secret |
| A13 | 单租户异常 | 让一个租户的配置/连接/reconcile 抛错 | 该租户按既有 reconciler 路径重连；其他租户和主进程继续运行 |
| A14 | 正常回复 | 发送一条可成功完成的文本消息 | 使用稳定 `stream_id`；正常中间帧和唯一 `finish=true` 最终帧发送；状态按既有成功路径迁移 |
| A15 | App/LLM 失败 | 让 App 流返回错误、异常或 900 秒超时 | 发送一次同一 `stream_id` 的固定安全 `finish=true` 帧；原始错误只在日志；WebSocket 可接收下一条消息；不标记成功状态 |
| A16 | 失败帧发送失败 | 让固定安全帧本身发送失败 | 不发送第二个补偿帧；processing 按既有失败路径重置一次；无 `DIFY_COMPLETED`/`REPLY_SENT` |
| A17 | 取消/断流 | 在流处理中取消 handler 或关闭 WebSocket | 不发送终止/补偿帧；停止 timer、feedback 和迟到事件；不写成功状态；processing 只重置一次 |
| A18 | 迟到事件竞态 | 取消后释放迟到的生产事件/回调 | 迟到 event、frame、final 不可见，不得恢复成功状态或写入队列造成永久阻塞 |
| A19 | 幂等重放 | 预置 `DIFY_COMPLETED` 或 `REPLY_SENT` 后重复收到同一消息 | 完成结果只重发而不再次调用 App；`REPLY_SENT` 直接跳过 |
| A20 | 长流默认值 | 不提供覆盖配置，运行接近超时的流 | 默认长流超时为 900 秒；拆分 service 不引入新的冲突默认值 |
| A21 | secret 边界 | 检查日志、Redis、状态、任务参数和 healthcheck 输出 | 不出现 secret、完整凭据或掩码 secret 被误用；原始 App 错误不进入对外帧 |
| A22 | 自动重启 | 让专用主进程异常退出 | Compose 按 `restart: always` 拉起；恢复后仍只有一个连接持有者 |
| A23 | 回滚 | 停止专用服务后切换旧 Compose/镜像并恢复通用开关 | 通用 worker 恢复长连接；专用服务停止；无并行连接、无迁移、无 key namespace 切换 |
| A24 | 兼容回归 | 运行现有 WeCom、Celery、Graph 和 API 回归 | 非企业微信业务队列、Graph 索引、API 和既有协议行为不因拓扑拆分改变 |

## Out of Scope

- 为每个租户创建独立容器或独立进程组。
- 通过多副本或新的 lease 算法实现高可用。
- 新增 Dockerfile、独立 API 镜像、独立 DB、Redis、Plugin Daemon、Sandbox、storage 或网络。
- 新增数据库 schema、Redis key namespace、状态字段或迁移脚本。
- 将长连接改造成 Celery 永久任务、API Web 进程任务或新的通用 Trigger provider。
- 保留旧通用长连接与新独立长连接并行运行以实现灰度或高可用。
- 新增 HTTP health endpoint、对外端口或第二套健康服务。
- 立即硬编码 CPU/内存 limits、reservations、autoscaling 或按租户资源隔离。
- 修改企业微信协议命令、`req_id` 透传、消息幂等键或既有 lease 算法。
- 改写 Dify App、Prompt、会话、模型、工具或知识库业务逻辑。
- 把取消、断流或发送失败转换为安全终止帧；这些路径明确不补帧。
- 执行数据库迁移、生产切换、回滚、提交、推送或部署。
- 修改任何其他需求的 canonical 编排 YAML，包括 `docs/engineering/workflows/2026-07-24-graphrag-extraction-schema-config-orchestration.yml`。

## Compatibility and Security Risks

| 风险 | 影响 | 约束/缓解 | 验收信号 |
| --- | --- | --- | --- |
| 新旧服务并行持有 Bot | 重复订阅、消息竞争、状态错乱 | 停止屏障、单副本、lease 三重约束；禁止并行切换 | 进程表、订阅日志和 lease 同时证明单所有者 |
| 模式变量配置错误 | 通用 worker 仍建立长连接或专用服务启动 Celery | 三个 service 显式设置 mode/开关，测试实际进程而非仅读 env | A02-A04 |
| 共享运行时遗漏 | 专用服务无法读取配置或调用 App/Plugin | 复用完整 env、依赖、mount、网络和基础服务 | A05 |
| 健康检查启动第二进程 | 检查本身造成重复连接 | 只检查已存在进程，不执行 worker 模块 | A12 |
| 单租户故障扩大为全局故障 | 全部租户同时失联 | reconciler/client 以租户为边界，异常重连不退出进程 | A07、A13 |
| lease/state 不兼容 | 重复处理、无法重试或无法回滚 | 不改 key namespace、schema 和状态迁移 | A09、A19、A23 |
| App 原始错误泄露 | 企业微信用户看到内部信息 | 固定安全文案；原文只入受控日志；禁止进入 Redis/状态/任务参数 | A15、A21 |
| 长流占用资源过久 | 单租户任务长期占用连接和线程 | 保持上一轮 900 秒默认值；监控和资源限制列入后续决策，不在本规格擅自新增机制 | A20 |
| SIGTERM 遗留连接 | 升级后旧连接与新连接竞争 | 正常关闭、释放 lease、启动前进程检查 | A10、A11 |
| 回滚顺序错误 | 新旧版本同时运行或旧版本无法接管 | 先停专用服务，再恢复通用 worker | A23 |
| 迁移职责混乱 | 重复迁移或启动时锁住服务 | 专用服务固定 `MIGRATION_ENABLED=false` | A06 |
| GitNexus 证据边界被误读 | 将不完整本地检索当作完整影响分析 | 所有代码事实标记 `local_search/non_gitnexus`，不声称有调用图 | 本规格证据章节 |

## Unconfirmed Items

以下项目没有被用户确认，不应在实现阶段自行猜测；应在任务拆分或实现前记录最终选择：

1. **Compose 的唯一事实源**：新增 service 应落在哪个生成入口、模板或部署脚本，以及生成文件是否需要同步维护。
2. **进程健康检查实现**：API 镜像内可用的进程检查工具、检查命令、`interval`、`timeout`、`retries`、`start_period` 和“无配置租户”时的健康判定。无论选择什么工具，都不得启动第二个 worker 或暴露 HTTP 端口。
3. **停止宽限期**：SIGTERM 后等待 active stream、连接关闭和 lease 释放的具体秒数，以及超时后的进程处置；本规格只锁定正常退出和不遗留连接。
4. **部署执行顺序**：现有发布工具能否在一次发布操作中提供“旧长连接停止确认”屏障，还是需要额外的运维脚本/CI 步骤。
5. **资源与观测配置**：专用 service 的 CPU/内存 limits、日志保留、指标、告警阈值和连接数面板；本期不硬编码资源限制。
6. **900 秒超时的配置归属**：上一轮已确认默认值为 900 秒，但其最终配置入口、与现有 processing TTL 的关系及测试注入 seam 需在实现时按现有修复确认，不能新增冲突的第二套超时。
7. **全局依赖不可用时的策略**：DB、Redis 或 Plugin Daemon 在专用服务启动阶段不可用时，是由进程退出交给 Compose 重启，还是由全局启动循环退避；租户级异常与全局初始化异常必须分开定义。
8. **回滚验证的最小运行手册**：需要确认旧镜像是否仍支持当前 endpoint 配置、状态 key 和固定安全错误语义，并明确发布人员的只读检查命令。

## Further Notes

- 本规格只定义外部契约、行为不变量和验收标准；不替代实现阶段对部署生成入口、健康检查工具和停止宽限期的最终确认。
- `docs/engineering/plans/wecom-dedicated-worker-gitnexus-inquiry.md` 记录了 GitNexus 不可用及 `local_search/non_gitnexus` 证据边界；`docs/engineering/plans/wecom-dedicated-worker-design.md` 记录了用户已确认的推荐决策。
- 既有企业微信同步流取消语义仍适用：取消、断流和发送失败不得让迟到事件恢复成功状态，也不得跨线程关闭不属于当前线程的生成器。
- 本文件是本阶段唯一正式规格产物。未调用状态写入 CLI，未提交节点状态，未修改生产代码、测试或 canonical 编排 YAML。
