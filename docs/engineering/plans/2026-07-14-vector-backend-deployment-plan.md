# OceanBase 向量后端部署修复计划

Last Updated: 2026-07-14

## 状态

- [x] 读取原始需求并确认当前分支、工作区和远端运行配置（`dev-spec-gen/references/workflow-guardrails.md`、`general-specs.md`）。
- [x] 代码反推向量后端注册链路：`VECTOR_STORE` → `vector_backend_registry` → `dify.vector_backends` entry point（`dev-spec-gen/references/testing-specs.md`）。
- [x] 修复 API 镜像构建，使 `dify-vdb-oceanbase` entry point 持久安装。
- [x] 修改 `deploy.sh` 生成的 Compose 配置，让 `ssrf_proxy` 持久加入外部 `docker_ragflow` 网络。
- [x] 修改 `deploy.sh` 生成运行时 `.env` 时保留 SSRF 私有地址白名单，并加入 `192.168.80.0/20`。
- [x] 构建并部署 API 镜像到远端 `1.15.0` 环境。
- [x] 验证 API/worker 可发现 `oceanbase` 后端，并核对代理访问 RAGFlow 不再返回连接类 503。

## 架构与边界

- 方案：保留现有 `uv sync --frozen --no-dev --no-editable` 以打包 `dify-agent`，并在同一虚拟环境显式安装 `vdb-oceanbase` workspace 包以注册 entry point；不修改向量工厂、数据库表结构或 API 契约。
- 网络方案：只将 `ssrf_proxy` 接入已存在的外部 `docker_ragflow` 网络；API、worker 不直接接入 RAGFlow 网络，保持 SSRF 代理边界。Squid 白名单同时允许 `10.1.14.0/24`、`10.1.250.0/24`、`192.168.80.0/20`。
- 复用：沿用 `api/pyproject.toml`、`api/uv.lock` 和现有 `api/Dockerfile` 构建链路。
- 不适用：前端文档、SQL 迁移、Jenkins、Java 规范。

## 验证证据

- 红测：构建前容器 entry point 列表中不存在 `oceanbase`。
- 黑测：构建后执行 `importlib.metadata`/向量后端注册查询，确认存在 `oceanbase`；API 与 worker 保持运行。
- 网络黑测：远端 `ssrf_proxy` 解析 `ragflow-cpu` 为 `192.168.80.5`，经代理访问返回 RAGFlow 的 `401`，Squid 日志为 `TCP_MISS/401 HIER_DIRECT/192.168.80.5`，不再是连接失败的 `503`。
- 边界：保留 `VECTOR_STORE=oceanbase`，验证不是通过切换到其他向量库规避异常。
- 网络边界：未加入 `docker_ragflow` 或未命中私有地址白名单时请求必须失败；配置完成后应至少证明 `ragflow-cpu` 可解析并经代理建立 HTTP 请求。
- 回归边界：API/worker 必须保持启动；镜像内不能只验证 OceanBase entry point 而遗漏 `agenton` 等现有运行时依赖。
