"""计算 Document 内容来源版本（source_version）。

设计目标
========

GraphRAG 索引必须能够识别“同一文档的内容发生了变化”，以便为变化后的内容
创建新的 Graph Index Job。Dify 上游的 `Document` 模型没有独立的、稳定的
内容版本字段（如 `content_hash`/`version`），因此本模块通过稳定哈希计算一个
`source_version`。

不变量
======

- 同一 Document 在内容未变化时必须产生相同的 `source_version`。
- Document 的 `updated_at`、`completed_at`、`batch`、`word_count` 中任一变化
  都必须产生新的 `source_version`。
- 计算结果只依赖传入的载荷，不读取数据库或全局状态，便于单元测试。
- 字段缺失时使用空字符串占位，保证跨数据库方言的稳定序列化。

为什么用 `updated_at.isoformat()` 而不是时间戳
----------------------------------------------

`updated_at` 可能是带时区或不带时区的 `datetime`，时间戳在不同方言下精度不
一；`isoformat()` 在去除时区后能稳定反映“该字段的值是否变化”，满足版本感知
的需求，且不引入额外的数据库列。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class DocumentSourceFacts:
    """参与 source_version 计算的稳定事实集合。

    字段刻意只读：所有字段缺失时归一化为空字符串，避免 None 影响序列化稳定
    性；调用方应传入 Document 的真实字段值。
    """

    document_id: str
    updated_at_iso: str
    completed_at_iso: str
    batch: str
    word_count: str

    def to_payload(self) -> dict[str, str]:
        """返回参与哈希的有序字典。键顺序固定，保证哈希稳定。"""
        return {
            "document_id": self.document_id,
            "updated_at": self.updated_at_iso,
            "completed_at": self.completed_at_iso,
            "batch": self.batch,
            "word_count": self.word_count,
        }


def _normalize_iso(value: datetime | None) -> str:
    """将 datetime 归一化为不含时区的 ISO 字符串，None 返回空串。"""
    if value is None:
        return ""
    if value.tzinfo is not None:
        value = value.astimezone(UTC).replace(tzinfo=None)
    return value.isoformat()


def build_source_facts(
    document_id: str,
    *,
    updated_at: datetime | None,
    completed_at: datetime | None,
    batch: str,
    word_count: int | None,
) -> DocumentSourceFacts:
    """从 Document 字段构造稳定事实集合。"""
    return DocumentSourceFacts(
        document_id=str(document_id),
        updated_at_iso=_normalize_iso(updated_at),
        completed_at_iso=_normalize_iso(completed_at),
        batch=str(batch or ""),
        word_count="" if word_count is None else str(word_count),
    )


def compute_source_version(facts: DocumentSourceFacts) -> str:
    """根据事实集合计算 64 位长度的 source_version。

    返回值为小写十六进制 sha256，长度固定为 64，适合存储在 `String(128)` 列。
    """
    payload = json.dumps(facts.to_payload(), sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
