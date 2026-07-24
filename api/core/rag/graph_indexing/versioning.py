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
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from operator import itemgetter
from typing import Any


@dataclass(frozen=True)
class SegmentSourceFacts:
    """参与 source_version 计算的单个有效 Segment 内容事实。"""

    segment_id: str
    content_sha256: str
    updated_at_iso: str

    def to_payload(self) -> dict[str, str]:
        return {
            "segment_id": self.segment_id,
            "content_sha256": self.content_sha256,
            "updated_at": self.updated_at_iso,
        }


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


def build_segment_source_facts(
    segment_id: str,
    *,
    content: str,
    updated_at: datetime | None,
) -> SegmentSourceFacts:
    """构造有效 Segment 的稳定内容事实，不保留原始正文。"""
    return SegmentSourceFacts(
        segment_id=str(segment_id),
        content_sha256=hashlib.sha256((content or "").encode("utf-8")).hexdigest(),
        updated_at_iso=_normalize_iso(updated_at),
    )


def compute_source_version(
    facts: DocumentSourceFacts,
    segment_facts: Sequence[SegmentSourceFacts] = (),
) -> str:
    """根据 Document 元数据和有效 Segment 内容计算 source_version。

    Segment 先按 ID 排序，因此数据库返回顺序不会导致版本抖动；正文只以 sha256
    进入载荷，避免日志或调试输出泄露知识库内容。
    """
    payload = {
        "document": facts.to_payload(),
        "segments": [item.to_payload() for item in sorted(segment_facts, key=lambda item: item.segment_id)],
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def compute_graph_version(config: Any) -> str:
    """根据规范化的节点图谱索引配置计算稳定版本。

    版本只包含会影响三元组抽取结果的 Schema 与抽取模型参数。实体、关系和
    三元组都按其稳定键排序，因此编辑器展示顺序变化不会触发不必要的重建。
    函数只使用传入配置，不访问数据库或全局状态。
    """
    if config.schema is None or config.extract_model_config is None:
        raise ValueError("enabled graph indexing requires schema and extract_model_config")

    schema = config.schema
    extract_model_config = config.extract_model_config
    payload = {
        "schema": {
            "entity_types": sorted(
                (
                    {
                        "name": item.name,
                        "properties": sorted(
                            (property_definition.model_dump(mode="json") for property_definition in item.properties),
                            key=itemgetter("name"),
                        ),
                    }
                    for item in schema.entity_types
                ),
                key=itemgetter("name"),
            ),
            "relation_types": sorted(
                (
                    {
                        "name": item.name,
                        "properties": sorted(
                            (property_definition.model_dump(mode="json") for property_definition in item.properties),
                            key=itemgetter("name"),
                        ),
                    }
                    for item in schema.relation_types
                ),
                key=itemgetter("name"),
            ),
            "allowed_triples": sorted(
                (
                    {
                        "source_type": item.source_type,
                        "relation_type": item.relation_type,
                        "target_type": item.target_type,
                    }
                    for item in schema.allowed_triples
                ),
                key=itemgetter("source_type", "relation_type", "target_type"),
            ),
        },
        "extract_model_config": {
            "provider": extract_model_config.provider,
            "model": extract_model_config.model,
            "temperature": extract_model_config.temperature,
            "max_tokens": extract_model_config.max_tokens,
            "max_triplets_per_chunk": extract_model_config.max_triplets_per_chunk,
            "strict": extract_model_config.strict,
        },
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()
