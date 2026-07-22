"""Shared document status updates for published knowledge-base Pipeline failures."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.dataset import Dataset, Document, Pipeline
from models.enums import IndexingStatus


def mark_document_error(
    session: Session,
    *,
    tenant_id: str,
    dataset_id: str,
    pipeline_id: str,
    document_id: str,
    error_message: str,
) -> bool:
    """Mark a matching document as failed without downgrading a completed document."""
    document = session.scalar(
        select(Document)
        .join(Dataset, Dataset.id == Document.dataset_id)
        .join(Pipeline, Pipeline.id == Dataset.pipeline_id)
        .where(
            Document.id == document_id,
            Document.tenant_id == tenant_id,
            Document.dataset_id == dataset_id,
            Dataset.tenant_id == tenant_id,
            Dataset.pipeline_id == pipeline_id,
            Pipeline.tenant_id == tenant_id,
        )
        .with_for_update()
        .limit(1)
    )
    if not document or document.indexing_status == IndexingStatus.COMPLETED:
        return False

    document.indexing_status = IndexingStatus.ERROR
    document.error = error_message
    session.add(document)
    session.commit()
    return True
