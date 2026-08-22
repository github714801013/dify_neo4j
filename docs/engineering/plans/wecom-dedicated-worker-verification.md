# 独立 WeCom Worker 最终验证记录

## 通过项

- 独立启动入口：`MODE=wecom_long_link` 直接执行 `python -m core.wecom_long_link.worker`；`MODE=worker` 仍按开关进入 Celery/supervisor 分支。
- Compose 静态契约：`worker` 与 `worker_graph` 关闭 WeCom；`wecom_worker` 开启 WeCom、关闭迁移、复用镜像/env/依赖/挂载/网络/restart，并有进程级 healthcheck。
- App failure：同一 stream 的唯一 `finish=true` 固定错误帧，发送后 callback 返回 `None`，客户端 reset processing，不写 `DIFY_COMPLETED`/`REPLY_SENT`，同时保持 WebSocket 继续监听。
- 取消、真实断流、企业微信发送失败仍不补伪造终止帧。
- `GUNICORN_TIMEOUT` 部署默认 900。

## 已执行验证

```text
指定 pytest 回归：26 passed
直接 asyncio Worker harness：正常流、App failure、timer、发送失败、cancel 全部通过
直接 client harness：App failure 后 reset_processing，completed=0，reply_sent=0
py_compile：通过
Ruff：通过
bash -n api/docker/entrypoint.sh：通过
bash -n docker/deploy.sh：通过
PyYAML Compose 解析：通过
git diff --check：通过
```

## 限制

- `verify` skill 文件不存在，不能声称执行该 skill。
- 本地 pytest 环境没有 `pytest-asyncio`，Worker/Client async 测试用同一测试夹具的直接 asyncio harness 验证；未安装依赖。
- 未执行真实 Docker Compose 重建、WebSocket 联调或远端部署。
- 当前工作区有大量历史无关修改，未纳入本任务验证或暂存。

## 修正记录

验证阶段发现 App failure 若返回错误文案会被 client 当成成功回复并写入完成状态；已改为发送终止帧后返回 `None`，恢复 processing 状态重置语义，同时不关闭 WebSocket。
