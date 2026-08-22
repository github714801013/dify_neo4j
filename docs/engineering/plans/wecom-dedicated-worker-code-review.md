# 独立 WeCom Worker 代码审查

## 审查范围

以当前 `HEAD` 为基线，审查本任务涉及的：

- `api/docker/entrypoint.sh`
- `docker/deploy.sh`、`docker/.env.example`
- `api/core/wecom_long_link/{worker,app_router,feedback}.py`
- WeCom 拓扑/失败帧回归测试
- 独立 Worker 规格与 tickets

工作区存在其他历史未提交改动，未将其作为本任务结论，也未执行全量暂存。

## Standards

- 启动入口复用现有 entrypoint，新增明确模式，不复制一套镜像或 supervisor。
- Worker 复用现有 DB/Redis/env/network/storage；没有新增状态表、租约算法或硬编码资源限制。
- App 原始错误只进入服务日志，企业微信使用固定安全文案，避免内部地址/协议细节泄露。
- `on_cancelled/on_disconnected` 既有断流语义未被改成伪造完成帧；App failure 使用独立终止路径。
- Ruff、Python 编译、Shell 语法与 Compose YAML 解析检查通过。

确认的 Standards 问题：0。

## Spec

- `wecom_worker` 直接执行 `core.wecom_long_link.worker`，不启动 Celery/supervisor。
- 通用 worker 与 graph worker 显式关闭 WeCom；新服务单副本承载全部租户并复用现有配置刷新、状态和 lease。
- 新服务不执行迁移，使用 `MIGRATION_ENABLED=false`；Compose 依赖、网络、挂载、restart 和进程级 healthcheck 已覆盖。
- App/LLM failure 会在同一 stream 发送唯一 `finish=true` 固定错误帧并保持连接；协议发送失败、取消和真实断流不补帧。
- `GUNICORN_TIMEOUT` 默认值已统一为 900。

确认的 Spec 问题：0。

## 已知验证限制

- 当前本地 pytest 环境没有 `pytest-asyncio`，worker async tests 无法由 pytest 插件执行；使用相同测试夹具的直接 asyncio harness 完成了正常流、App failure、timer、发送失败和取消回归。
- 未部署或执行真实 Compose 重建；已用 PyYAML 解析 deploy.sh 生成的 Compose 片段并验证服务拓扑。

结论：未发现阻断合并的问题；提交时必须只暂存本任务文件，不能带入工作区既有无关改动。
