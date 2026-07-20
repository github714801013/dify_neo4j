"""基础检索与 Graph Retrieval 的加权 RRF 融合。

``graph_weight`` 表示图排名贡献比例，基础排名使用 ``1 - graph_weight``。
相同 ``doc_id`` 只返回一次，并保留图路径、命中实体和来源元数据。只有存在图
候选时才改写 score；图关闭或无结果时原样返回上游文档。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from core.rag.models.document import Document

_RRF_K = 60


@dataclass
class _Candidate:
    document: Document
    first_seen: int
    raw_score: float = 0
    has_base: bool = False
    has_graph: bool = False


def fuse_retrieval_documents(
    base_documents: list[Document],
    graph_documents: list[Document],
    *,
    graph_weight: float,
    top_k: int,
) -> list[Document]:
    """按加权 RRF 融合两路候选，并将融合分数归一化到 ``0..1``。"""
    if not graph_documents or graph_weight <= 0:
        return base_documents[:top_k] if top_k else list(base_documents)

    normalized_graph_weight = min(1.0, max(0.0, float(graph_weight)))
    base_weight = 1.0 - normalized_graph_weight
    candidates: dict[str, _Candidate] = {}
    sequence = 0

    for rank, document in enumerate(base_documents, start=1):
        if base_weight <= 0:
            break
        sequence += 1
        key = _candidate_key(document)
        candidate = candidates.get(key)
        if candidate is None:
            candidate = _Candidate(document=document.model_copy(deep=True), first_seen=sequence)
            candidates[key] = candidate
        candidate.has_base = True
        candidate.raw_score += base_weight / (_RRF_K + rank)

    for rank, document in enumerate(graph_documents, start=1):
        sequence += 1
        key = _candidate_key(document)
        candidate = candidates.get(key)
        if candidate is None:
            candidate = _Candidate(document=document.model_copy(deep=True), first_seen=sequence)
            candidates[key] = candidate
        else:
            _merge_graph_metadata(candidate.document, document)
        candidate.has_graph = True
        candidate.raw_score += normalized_graph_weight / (_RRF_K + rank)

    ranked = sorted(candidates.values(), key=lambda item: (-item.raw_score, item.first_seen))
    if top_k:
        ranked = ranked[:top_k]
    if not ranked:
        return []

    max_score = max(item.raw_score for item in ranked) or 1.0
    result: list[Document] = []
    for item in ranked:
        sources: list[str] = []
        if item.has_base:
            sources.append("base")
        if item.has_graph:
            sources.append("graph")
        item.document.metadata["retrieval_sources"] = sources
        item.document.metadata["score"] = item.raw_score / max_score
        item.document.metadata["fusion_method"] = "weighted_rrf"
        item.document.metadata["graph_weight"] = normalized_graph_weight
        result.append(item.document)
    return result


def _candidate_key(document: Document) -> str:
    metadata = document.metadata
    doc_id = str(metadata.get("doc_id") or "").strip()
    if doc_id:
        return f"doc:{doc_id}"
    segment_id = str(metadata.get("segment_id") or "").strip()
    if segment_id:
        return f"segment:{segment_id}"
    payload = "\x1f".join(
        (
            str(metadata.get("dataset_id") or ""),
            str(metadata.get("document_id") or ""),
            document.page_content,
        )
    )
    return f"content:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def _merge_graph_metadata(target: Document, graph_document: Document) -> None:
    for key, value in graph_document.metadata.items():
        if key.startswith("graph_") or key in {"segment_id", "retrieval_source"}:
            target.metadata[key] = value


__all__ = ["fuse_retrieval_documents"]
