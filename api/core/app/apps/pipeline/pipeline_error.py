"""User-facing error formatting for published knowledge-base Pipeline tasks."""

from pydantic import ValidationError

PIPELINE_EXECUTION_ERROR_MESSAGE = "知识库 Pipeline 执行失败，请查看 Worker 日志"


def format_pipeline_document_error(error: BaseException | str) -> str:
    """Convert an execution exception into a concise document error message."""
    if isinstance(error, ValidationError):
        first_error = error.errors()[0]
        location = ".".join(str(item) for item in first_error.get("loc", ()))
        if location:
            return f"知识库 Pipeline 配置校验失败：{location}"
        return "知识库 Pipeline 配置校验失败，请查看 Worker 日志"
    return PIPELINE_EXECUTION_ERROR_MESSAGE
