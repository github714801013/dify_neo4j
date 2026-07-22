"""Detect terminal failures returned as text by the MinerU parse-file plugin tool.

The plugin currently serializes upstream HTTP failures as ordinary text messages.
This module keeps the compatibility check isolated from the workflow runtime so
that a future plugin response contract can be updated without changing the
workflow execution boundary.
"""

from __future__ import annotations

import json
import logging

from core.tools.errors import ToolInvokeError

logger = logging.getLogger(__name__)

MINERU_PROVIDER_NAME = "langgenius/mineru/mineru"
MINERU_PARSE_FILE_TOOL_NAME = "parse-file"
MINERU_PARSE_FAILURE_MESSAGE = "MinerU file parsing failed"
_MINERU_PARSE_FAILURE_PREFIX = "Failed to parse file. result:"
_MINERU_CONNECTION_FAILURE_PREFIX = "Failed to connect to server:"


def raise_if_mineru_parse_failed(*, provider_name: str, tool_name: str | None, message: object) -> None:
    """Raise a concise tool error when MinerU reports a terminal parse failure.

    The original plugin response is written to the Worker log for diagnosis,
    while the raised exception deliberately contains only a stable summary so
    that it cannot be persisted as document content or a document error detail.
    """
    if provider_name != MINERU_PROVIDER_NAME or tool_name != MINERU_PARSE_FILE_TOOL_NAME:
        return
    if not isinstance(message, str):
        return

    failure = False
    if message.startswith(_MINERU_CONNECTION_FAILURE_PREFIX):
        failure = True
    elif message.startswith(_MINERU_PARSE_FAILURE_PREFIX):
        raw_result = message[len(_MINERU_PARSE_FAILURE_PREFIX) :].strip()
        try:
            result = json.loads(raw_result)
        except json.JSONDecodeError:
            failure = True
        else:
            failure = isinstance(result, dict) and (result.get("status") == "failed" or bool(result.get("error")))

    if not failure:
        return

    logger.error(
        "MinerU parse-file returned a failure response: provider=%s tool=%s result=%s",
        provider_name,
        tool_name,
        message,
    )
    raise ToolInvokeError(MINERU_PARSE_FAILURE_MESSAGE)
