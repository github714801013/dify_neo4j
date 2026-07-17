# OceanBase 向量后端部署异常：原始需求

## 来源

- 日期：2026-07-14
- 来源：用户提供的企业微信截图及对话
- 原始需求：`dev-spec-gen 修复这个异常`
- 附件语义：Dify 数据集页面提示 `Vector store 'oceanbase' is not supported. No backends installed.`，并建议安装插件或注册 `dify.vector_backends` entry point。

## 验收目标

- `VECTOR_STORE=oceanbase` 时，API 镜像内可发现 `oceanbase` 向量后端 entry point。
- Dify API、worker 能正常加载 OceanBase 向量工厂。
- 不修改 OceanBase 向量检索业务逻辑，不改变远端数据库配置。
- Dify 的 `ssrf_proxy` 重新部署后仍能访问 RAGFlow 的 `ragflow-cpu` Docker 网络别名。
- SSRF 私有地址白名单必须在部署脚本生成的远端 `.env` 中保留，并覆盖 Dify、OceanBase 与 RAGFlow 所需网段：`10.1.14.0/24`、`10.1.250.0/24`、`192.168.80.0/20`。

## 已确认事实

- 当前分支：`1.15.0`。
- 源码存在 `api/providers/vdb/vdb-oceanbase`，并在 `api/pyproject.toml` 的 `vdb-all` 中声明。
- 远端 API 容器配置为 `VECTOR_STORE=oceanbase`，但运行环境没有安装 `dify-vdb-oceanbase` entry point。
- 远端 `ragflow-cpu` 位于外部 Docker 网络 `docker_ragflow`，Dify 的 `ssrf_proxy` 默认未加入该网络。
- 之前的 `deploy.sh` 未把 `SSRF_PROXY_ALLOW_PRIVATE_IPS` 写入远端运行时 `.env`，导致 Squid 生成的私有地址白名单为空。
- 移除 `--no-editable` 后 API 镜像启动暴露 `ModuleNotFoundError: No module named 'agenton'`；因此必须保留非 editable 安装以打包 `dify-agent`，再显式安装 OceanBase workspace 包以注册 entry point。

## 待确认与不适用项

- 没有 Jira/其他 tracker 事项配置，使用本地 raw/plan 作为本轮状态源。
- 前端实现文档不适用：本次只修复后端镜像依赖打包，不改变接口字段或页面契约。
- 数据库写入不适用：只验证运行时包发现和服务启动，不修改业务数据。
