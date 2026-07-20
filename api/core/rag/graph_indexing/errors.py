"""GraphRAG 索引 Job 链路的领域错误类型。

这些异常用于在 Reconciler/Worker 内部表达可预期的业务失败，便于上层
统一记录错误码并决定重试或标记失败。所有异常均保持轻量，不承载敏感数据。
"""


class GraphIndexError(Exception):
    """GraphRAG 索引链路的基类错误。"""

    code: str = "graph_index_error"

    def __init__(self, message: str = "", *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code

    @property
    def error_message(self) -> str:
        """返回人类可读的错误描述，便于写入 Job 的 last_error_message。"""
        message = self.args[0] if self.args else ""
        return str(message) if message else self.code


class GraphIndexJobClaimError(GraphIndexError):
    """Worker 领取 Job 时发现 Job 已不在 pending/不可领取。"""

    code = "graph_index_job_not_claimable"


class GraphIndexJobConflictError(GraphIndexError):
    """并发创建 Job 时唯一键冲突，调用方应视为幂等成功。"""

    code = "graph_index_job_conflict"


class GraphIndexJobNotFoundError(GraphIndexError):
    """按 ID 未找到对应的 Graph Index Job。"""

    code = "graph_index_job_not_found"
