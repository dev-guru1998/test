from __future__ import annotations

import os
import threading
import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from gateway.core import (
    GatewayError,
    GraphRegistry,
    GraphRunner,
    NotFoundError,
    answer_with_document_citations,
    build_query_citations,
    documents_used_from_citations,
    utc_now,
)


GRAPH_STORE_DIR = os.environ.get("GRAPH_STORE_DIR", "./data/graphs")
ADMIN_TOKEN = os.environ.get("GRAPH_GATEWAY_ADMIN_TOKEN", "")
DRY_RUN = os.environ.get("GRAPH_GATEWAY_DRY_RUN", "false").lower() in {"1", "true", "yes", "on"}

registry = GraphRegistry(GRAPH_STORE_DIR)
runner = GraphRunner(dry_run=DRY_RUN)

app = FastAPI(
    title="GraphRAG Gateway",
    version="0.1.0",
    description=(
        "OpenAPI-compatible gateway for admin-curated Microsoft GraphRAG "
        "collections exposed to Open WebUI tools."
    ),
)


class CreateCollectionRequest(BaseModel):
    name: str = Field(..., min_length=1)
    description: str = ""
    id: str | None = Field(default=None, description="Optional stable collection id.")


class FindCollectionRequest(BaseModel):
    query: str = Field(..., min_length=1)
    limit: int = Field(default=5, ge=1, le=20)


class QueryCollectionRequest(BaseModel):
    question: str = Field(..., min_length=1)
    method: str = Field(default="local", pattern="^(local|global|drift|basic)$")


class IndexRequest(BaseModel):
    method: str = Field(default="standard", pattern="^(standard|fast)$")
    confirm: bool = Field(
        default=False,
        description="Must be true because indexing can be expensive and long-running.",
    )


class ConfirmRequest(BaseModel):
    confirm: bool = Field(default=False)


def require_admin(
    x_admin_token: Annotated[str | None, Header(alias="X-Admin-Token")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    if not ADMIN_TOKEN:
        return
    bearer = ""
    if authorization and authorization.lower().startswith("bearer "):
        bearer = authorization.split(" ", 1)[1].strip()
    if x_admin_token != ADMIN_TOKEN and bearer != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Admin token is required.")


def http_error(exc: GatewayError) -> HTTPException:
    status = getattr(exc, "status_code", 400)
    return HTTPException(status_code=status, detail=str(exc))


def public_collection(collection_id: str) -> dict:
    manifest = registry.load(collection_id)
    if not manifest.get("published"):
        raise NotFoundError(f"Published collection not found: {collection_id}")
    return manifest


@app.get("/health", include_in_schema=False)
def health() -> dict:
    return {"ok": True, "graph_store_dir": str(registry.root), "dry_run": DRY_RUN}


@app.get("/v1/collections", operation_id="graphrag_list_collections")
def list_collections() -> dict:
    return {"collections": registry.list_collections(published_only=True)}


@app.get("/v1/collections/{collection_id}", operation_id="graphrag_describe_collection")
def describe_collection(collection_id: str) -> dict:
    try:
        return public_collection(collection_id)
    except GatewayError as exc:
        raise http_error(exc) from exc


@app.post("/v1/collections/find", operation_id="graphrag_find_collection")
def find_collection(request: FindCollectionRequest) -> dict:
    return {"collections": registry.find_collections(request.query, request.limit)}


@app.post("/v1/collections/{collection_id}/query", operation_id="graphrag_query_collection")
def query_collection(collection_id: str, request: QueryCollectionRequest) -> dict:
    try:
        manifest = public_collection(collection_id)
        project_dir = registry.project_dir(collection_id)
        answer = runner.query(project_dir, request.method, request.question)
        citations = build_query_citations(project_dir, answer)
        return {
            "collection": {
                "id": manifest["id"],
                "name": manifest["name"],
                "last_indexed_at": manifest.get("last_indexed_at"),
            },
            "method": request.method,
            "answer": answer,
            "answer_with_citations": answer_with_document_citations(answer, citations),
            "citations": citations,
            "documents_used": documents_used_from_citations(citations),
        }
    except GatewayError as exc:
        raise http_error(exc) from exc


@app.get("/v1/admin/collections", dependencies=[Depends(require_admin)], include_in_schema=False)
def admin_list_collections() -> dict:
    return {"collections": registry.list_collections(published_only=False)}


@app.post("/v1/admin/collections", dependencies=[Depends(require_admin)], include_in_schema=False)
def admin_create_collection(request: CreateCollectionRequest) -> dict:
    try:
        return registry.create_collection(request.name, request.description, request.id)
    except GatewayError as exc:
        raise http_error(exc) from exc


@app.post(
    "/v1/admin/collections/{collection_id}/documents",
    dependencies=[Depends(require_admin)],
    include_in_schema=False,
)
async def admin_add_documents(
    collection_id: str,
    files: Annotated[list[UploadFile], File(description="Source documents to place under project/input.")],
    overwrite: Annotated[bool, Form()] = False,
) -> dict:
    try:
        manifest = registry.load(collection_id)
        added: list[str] = []
        for upload in files:
            content = await upload.read()
            manifest = registry.add_document_bytes(collection_id, upload.filename or "document.txt", content, overwrite)
            added.append(upload.filename or "document.txt")
        return {"collection": manifest, "added": added}
    except GatewayError as exc:
        raise http_error(exc) from exc


@app.post(
    "/v1/admin/collections/{collection_id}/import-pack",
    dependencies=[Depends(require_admin)],
    include_in_schema=False,
)
async def admin_import_pack(
    collection_id: str,
    file: Annotated[UploadFile, File(description="Complete GraphRAG project pack: zip, tar, tar.gz, or tgz.")],
    overwrite: Annotated[bool, Form()] = False,
) -> dict:
    suffix = Path(file.filename or "pack.tar.gz").suffix or ".pack"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
        tmp_path = Path(handle.name)
        handle.write(await file.read())
    try:
        return registry.import_pack(collection_id, tmp_path, overwrite=overwrite)
    except GatewayError as exc:
        raise http_error(exc) from exc
    finally:
        tmp_path.unlink(missing_ok=True)


@app.get(
    "/v1/admin/collections/{collection_id}/validate",
    dependencies=[Depends(require_admin)],
    include_in_schema=False,
)
def admin_validate_collection(collection_id: str) -> dict:
    try:
        return registry.validate_collection(collection_id)
    except GatewayError as exc:
        raise http_error(exc) from exc


def run_index_job(collection_id: str, job_id: str, method: str) -> None:
    job = registry.load_job(collection_id, job_id)
    log_path = Path(job["log_path"])
    try:
        job["status"] = "running"
        job["started_at"] = job["started_at"] or utc_now()
        registry.save_job(collection_id, job)
        manifest = registry.load(collection_id)
        manifest["status"] = "indexing"
        manifest["last_job"] = {"id": job_id, "status": "running", "type": "index", "method": method}
        registry.save(collection_id, manifest)
        runner.index(registry.project_dir(collection_id), method, log_path)
        manifest = registry.load(collection_id)
        manifest["status"] = "indexed"
        manifest["last_indexed_at"] = utc_now()
        manifest["model_metadata"] = registry.default_model_metadata()
        manifest["last_job"] = {"id": job_id, "status": "succeeded", "type": "index", "method": method}
        registry.save(collection_id, manifest)
        job["status"] = "succeeded"
        job["finished_at"] = utc_now()
        registry.save_job(collection_id, job)
    except Exception as exc:  # noqa: BLE001 - job runner must persist all failures.
        job["status"] = "failed"
        job["error"] = str(exc)
        job["finished_at"] = utc_now()
        registry.save_job(collection_id, job)
        try:
            manifest = registry.load(collection_id)
            manifest["status"] = "failed"
            manifest["last_job"] = {"id": job_id, "status": "failed", "type": "index", "method": method}
            registry.save(collection_id, manifest)
        except GatewayError:
            pass


@app.post(
    "/v1/admin/collections/{collection_id}/index",
    dependencies=[Depends(require_admin)],
    include_in_schema=False,
)
def admin_start_index(collection_id: str, request: IndexRequest) -> dict:
    try:
        if not request.confirm:
            raise GatewayError("Indexing requires confirm=true.")
        job = registry.create_job(collection_id, "index", request.method)
        thread = threading.Thread(
            target=run_index_job,
            args=(collection_id, job["id"], request.method),
            daemon=True,
        )
        thread.start()
        return {"job": registry.load_job(collection_id, job["id"])}
    except GatewayError as exc:
        raise http_error(exc) from exc


@app.get(
    "/v1/admin/collections/{collection_id}/jobs/{job_id}",
    dependencies=[Depends(require_admin)],
    include_in_schema=False,
)
def admin_index_status(collection_id: str, job_id: str) -> dict:
    try:
        return {"job": registry.load_job(collection_id, job_id)}
    except GatewayError as exc:
        raise http_error(exc) from exc


@app.post(
    "/v1/admin/collections/{collection_id}/publish",
    dependencies=[Depends(require_admin)],
    include_in_schema=False,
)
def admin_publish_collection(collection_id: str, request: ConfirmRequest) -> dict:
    try:
        return registry.publish_collection(collection_id, confirm=request.confirm)
    except GatewayError as exc:
        raise http_error(exc) from exc


@app.post(
    "/v1/admin/collections/{collection_id}/unpublish",
    dependencies=[Depends(require_admin)],
    include_in_schema=False,
)
def admin_unpublish_collection(collection_id: str, request: ConfirmRequest) -> dict:
    try:
        return registry.unpublish_collection(collection_id, confirm=request.confirm)
    except GatewayError as exc:
        raise http_error(exc) from exc


@app.post(
    "/v1/admin/collections/{collection_id}/export-pack",
    dependencies=[Depends(require_admin)],
    include_in_schema=False,
)
def admin_export_pack(collection_id: str) -> FileResponse:
    try:
        archive_path = registry.export_pack(collection_id)
        return FileResponse(
            archive_path,
            media_type="application/gzip",
            filename=archive_path.name,
        )
    except GatewayError as exc:
        raise http_error(exc) from exc
