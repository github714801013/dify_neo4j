# Dify 自建环境部署脚本原始需求

Last Updated: 2026-07-16

## 来源

- 用户补充：`D:\workplace\python\dify\docker 需要同步到本地的部署脚本`。
- 前置故障：知识库图索引插件通过 plugin daemon 反向调用 LLM 时，`POST /inner/api/invoke/llm` 返回 404。
- 当前任务未关联 Jira；本地 raw requirements 与 plan 是本轮状态源。

## 已确认事实

- 标准 `docker/docker-compose.yaml` 已为 API 和 worker 映射：
  `INNER_API_KEY_FOR_PLUGIN: ${PLUGIN_DIFY_INNER_API_KEY...}`。
- 实际部署入口是 `docker/deploy.sh`，它不会直接同步标准 Compose，而是通过
  `write_compose_file` 生成精简的 `docker-compose.origin.yaml` 并上传远端。
- 生成的 API/worker `environment` 目前只有 `MODE`，缺少 `INNER_API_KEY_FOR_PLUGIN`。
- 运行中的 plugin daemon 有 `DIFY_INNER_API_KEY`，API 容器没有
  `INNER_API_KEY_FOR_PLUGIN`；Dify 内部鉴权因此主动返回 404。
- 远端 `.env` 已包含 `PLUGIN_DIFY_INNER_API_KEY`，问题不是密钥源缺失，而是部署脚本没有把它映射到 API 实际读取的变量名。

## 本轮要求

- 将标准 Compose 中的 `INNER_API_KEY_FOR_PLUGIN` 映射同步到 `docker/deploy.sh`
  生成的 `docker-compose.origin.yaml`。
- API 和 worker 均保持与标准 `docker/docker-compose.yaml` 一致。
- 不全量复制标准 Compose，不改变数据库、Redis、网络、镜像构建或插件源码。
- 保持现有 `.env` 变量名 `PLUGIN_DIFY_INNER_API_KEY`，不新增第二份密钥配置。
- 图索引工作流包含 263 个分段、每批处理 16 个分段，单次工作流累计耗时超过 Dify 默认的 1200 秒限制。
- 在 `docker/deploy.sh` 中为 `APP_MAX_EXECUTION_TIME` 和
  `WORKFLOW_MAX_EXECUTION_TIME` 提供可由本地 `.env` 覆盖的默认值 `3600`，并写入远端运行时 `.env`。
- 仅重建依赖这两个变量的 API 与 worker 服务，不调整插件单次调用超时。
- plugin daemon 的 Gitea 同步宿主机目录使用 `GITEA_SYNC_HOST_TEMP_DIR` 配置；当前部署值放在 `docker/.env`，不得在 Compose 中硬编码 `/home/ji99` 路径。

## 验收标准

- 调用 `write_compose_file` 生成的 Compose 中：
  - `services.api.environment.INNER_API_KEY_FOR_PLUGIN` 引用 `PLUGIN_DIFY_INNER_API_KEY`；
  - `services.worker.environment.INNER_API_KEY_FOR_PLUGIN` 引用同一变量；
  - `services.plugin_daemon.environment.DIFY_INNER_API_KEY` 继续引用同一变量。
- `docker/deploy.sh` 通过 Bash 语法检查。
- 生成的 Compose 通过 `docker compose config` 解析。
- 代码修改仅限部署脚本与本轮 raw/plan；远端部署与容器重启仅在用户后续明确授权后执行。
- 部署后 API 健康检查返回 200，API、worker 与 plugin daemon 的内部密钥已注入且一致。
- 使用正确内部密钥请求 `POST /inner/api/invoke/llm` 时，不再返回内部鉴权 404。
- 生成的远端运行时 `.env` 同时包含：
  - `APP_MAX_EXECUTION_TIME=3600`；
  - `WORKFLOW_MAX_EXECUTION_TIME=3600`。
- 重建后 API 与 worker 容器中的两个变量均为 `3600`，API 健康检查返回 200。
- `docker-compose.yaml` 与 `docker-compose-template.yaml` 均通过 `GITEA_SYNC_HOST_TEMP_DIR` 配置 plugin daemon 的环境变量和目录挂载。

## 测试 seam

- 公开 seam：加载 `docker/deploy.sh` 中的函数但不执行 `main`，调用
  `write_compose_file` 生成临时 Compose，再从生成结果验证 API、worker 和 plugin daemon 的密钥映射。
- 红测：修改前生成结果缺少 API/worker 的 `INNER_API_KEY_FOR_PLUGIN`。
- 黑测：修改后生成结果包含三处一致映射，并能通过 Compose 配置解析。

## 不适用项

- `setup-matt-pocock-skills` 未被显式调用；本仓库已有本地 raw/plan 约定，本轮沿用本地状态源。
- `implement` 未被显式调用；用户已直接授权修改部署脚本。
- 不涉及前端契约、SQL/Flyway、业务数据写入或插件重新打包。

## 迭代记录

- 2026-07-14：补录 plugin daemon 反向调用密钥未同步到自定义部署 Compose 的问题。
- 2026-07-14：用户明确授权“执行部署”，允许运行本地 `docker/deploy.sh` 完成镜像构建、远端文件同步、镜像加载和 Compose 更新。
- 2026-07-14：部署完成；API 健康检查为 200，三处内部密钥长度均为 30 且值一致，最小内部调用返回参数校验 400 而非鉴权 404。
- 2026-07-15：用户选择快速解除图索引工作流 1200 秒总时长限制，授权调整部署脚本并部署到现有自建环境。
- 2026-07-16：将 Gitea 同步宿主机目录从 Compose 固定路径改为 `.env` 变量。
