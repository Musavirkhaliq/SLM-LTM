from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, File, UploadFile

from slm_common.auth import Principal
from slm_common.errors import ApiError
from slm_common.pagination import PageParams
from slm_common.rbac import Role, require_role
from slm_rag.schemas import (
    ALLOWED_MIME_TYPES,
    MAX_DOCUMENT_BYTES,
    DocumentOut,
    IndexCreate,
    IndexOut,
    QueryIn,
    QueryOut,
)

router = APIRouter(prefix="/api/v1/rag", tags=["rag"])


@router.get("/indexes")
async def list_indexes(page: PageParams = Depends(), _: Principal = require_role(Role.viewer)):
    raise ApiError.not_implemented("Listing RAG indexes")


@router.post("/indexes", response_model=IndexOut, status_code=201)
async def create_index(body: IndexCreate, _: Principal = require_role(Role.ml_engineer)):
    raise ApiError.not_implemented("Creating RAG indexes")


@router.post("/indexes/{index_id}/documents", response_model=DocumentOut, status_code=202)
async def upload_document(
    index_id: UUID,
    file: UploadFile = File(...),
    _: Principal = require_role(Role.ml_engineer),
):
    if not file.filename:
        raise ApiError(422, "validation_error", "file must have a filename", param="file")
    mime = (file.content_type or "").split(";")[0].strip().lower()
    if mime not in ALLOWED_MIME_TYPES:
        raise ApiError(
            415, "unsupported_media_type", f"'{mime or 'unknown'}' documents are not supported"
        )
    if file.size is not None and file.size > MAX_DOCUMENT_BYTES:
        raise ApiError(413, "document_too_large", f"Documents are limited to {MAX_DOCUMENT_BYTES} bytes")
    raise ApiError.not_implemented("Ingesting documents")


@router.post("/indexes/{index_id}/query", response_model=QueryOut)
async def query_index(index_id: UUID, body: QueryIn, _: Principal = require_role(Role.developer)):
    raise ApiError.not_implemented("Querying RAG indexes")
