from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


QUERY_METHODS = {"local", "global", "drift", "basic"}
INDEX_METHODS = {"standard", "fast"}
DATA_REFERENCE_RE = re.compile(r"\[Data:\s*([^\]]+)\]", re.IGNORECASE)
SOURCES_SEGMENT_RE = re.compile(r"\bSources\s*\(([^)]*)\)", re.IGNORECASE)
NUMBER_RE = re.compile(r"\d+")
MAX_CITATION_SNIPPET_CHARS = 500


class GatewayError(RuntimeError):
    status_code = 400


class NotFoundError(GatewayError):
    status_code = 404


class ConflictError(GatewayError):
    status_code = 409


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def collection_id_from_name(name: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_-]+", "-", name.strip().lower()).strip("-")
    value = re.sub(r"-{2,}", "-", value)
    if not value:
        raise GatewayError("Collection id cannot be empty.")
    if len(value) > 80:
        value = value[:80].rstrip("-_")
    return value


def validate_collection_id(collection_id: str) -> str:
    normalized = collection_id_from_name(collection_id)
    if normalized != collection_id:
        raise GatewayError(
            "Collection id must use only lowercase letters, numbers, hyphens, and underscores."
        )
    return normalized


def safe_filename(name: str) -> str:
    filename = Path(name).name.strip()
    filename = re.sub(r"[^a-zA-Z0-9._ -]+", "_", filename)
    if not filename or filename in {".", ".."}:
        raise GatewayError("Uploaded file has an invalid name.")
    return filename


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def file_count(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for item in path.rglob("*") if item.is_file())


def directory_has_files(path: Path) -> bool:
    return path.exists() and any(item.is_file() for item in path.rglob("*"))


def extract_source_reference_ids(answer: str) -> list[int]:
    seen: set[int] = set()
    source_ids: list[int] = []
    for data_match in DATA_REFERENCE_RE.finditer(answer):
        for sources_match in SOURCES_SEGMENT_RE.finditer(data_match.group(1)):
            for number_match in NUMBER_RE.finditer(sources_match.group(1)):
                source_id = int(number_match.group(0))
                if source_id not in seen:
                    seen.add(source_id)
                    source_ids.append(source_id)
    return source_ids


def citation_snippet(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip())[:MAX_CITATION_SNIPPET_CHARS]


def load_source_citation_index(project_dir: Path) -> dict[int, dict[str, Any]]:
    output_dir = project_dir / "output"
    text_units_path = output_dir / "text_units.parquet"
    documents_path = output_dir / "documents.parquet"
    if not text_units_path.exists() or not documents_path.exists():
        return {}

    try:
        import pandas as pd

        text_units = pd.read_parquet(text_units_path)
        documents = pd.read_parquet(documents_path)
    except Exception:
        return {}

    required_text_unit_columns = {"human_readable_id", "id", "text", "document_id"}
    required_document_columns = {"human_readable_id", "id", "title"}
    if not required_text_unit_columns.issubset(text_units.columns) or not required_document_columns.issubset(
        documents.columns
    ):
        return {}

    documents_by_id = {str(row["id"]): row for _, row in documents.iterrows()}
    citations: dict[int, dict[str, Any]] = {}
    for _, text_unit in text_units.iterrows():
        document_id = str(text_unit["document_id"])
        document = documents_by_id.get(document_id)
        if document is None:
            continue
        try:
            source_id = int(text_unit["human_readable_id"])
            document_source_id = int(document["human_readable_id"])
        except (TypeError, ValueError):
            continue
        citations[source_id] = {
            "source_id": source_id,
            "text_unit_id": str(text_unit["id"]),
            "document_id": document_id,
            "document_source_id": document_source_id,
            "document": str(document["title"]),
            "snippet": citation_snippet(text_unit["text"]),
        }
    return citations


def build_query_citations(project_dir: Path, answer: str) -> list[dict[str, Any]]:
    citation_index = load_source_citation_index(project_dir)
    return [
        citation_index[source_id]
        for source_id in extract_source_reference_ids(answer)
        if source_id in citation_index
    ]


def documents_used_from_citations(citations: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    documents: list[str] = []
    for citation in citations:
        document = str(citation.get("document", "")).strip()
        if document and document not in seen:
            seen.add(document)
            documents.append(document)
    return documents


def answer_with_document_citations(answer: str, citations: list[dict[str, Any]]) -> str:
    citations_by_source_id = {citation["source_id"]: citation for citation in citations}

    def annotate(match: re.Match[str]) -> str:
        documents = []
        seen_documents: set[str] = set()
        for sources_match in SOURCES_SEGMENT_RE.finditer(match.group(1)):
            for number_match in NUMBER_RE.finditer(sources_match.group(1)):
                citation = citations_by_source_id.get(int(number_match.group(0)))
                if citation is None:
                    continue
                document = str(citation.get("document", "")).strip()
                if document and document not in seen_documents:
                    seen_documents.add(document)
                    documents.append(document)
        if not documents:
            return match.group(0)
        return f"{match.group(0)} [Documents: {'; '.join(documents)}]"

    return DATA_REFERENCE_RE.sub(annotate, answer)


def safe_extract_tar(archive: tarfile.TarFile, target: Path) -> None:
    target_resolved = target.resolve()
    for member in archive.getmembers():
        destination = (target / member.name).resolve()
        if target_resolved not in destination.parents and destination != target_resolved:
            raise GatewayError(f"Archive member escapes target directory: {member.name}")
    try:
        archive.extractall(target, filter="data")
    except TypeError:
        archive.extractall(target)


def safe_extract_zip(archive: zipfile.ZipFile, target: Path) -> None:
    target_resolved = target.resolve()
    for member in archive.namelist():
        destination = (target / member).resolve()
        if target_resolved not in destination.parents and destination != target_resolved:
            raise GatewayError(f"Archive member escapes target directory: {member}")
    archive.extractall(target)


def find_project_root(extracted: Path) -> Path:
    candidates = [extracted]
    candidates.extend(item for item in extracted.iterdir() if item.is_dir())
    for candidate in candidates:
        project = candidate / "project"
        if project.is_dir() and ((project / "settings.yaml").exists() or (project / "input").is_dir()):
            return project
        if (candidate / "settings.yaml").exists() or (candidate / "input").is_dir():
            return candidate
    raise GatewayError(
        "Imported pack must contain a GraphRAG project folder with settings.yaml or input/."
    )


class GraphRegistry:
    def __init__(self, graph_store_dir: str | Path, env: dict[str, str] | None = None):
        self.root = Path(graph_store_dir).expanduser().resolve()
        self.env = env or os.environ
        self.root.mkdir(parents=True, exist_ok=True)

    def collection_dir(self, collection_id: str) -> Path:
        validate_collection_id(collection_id)
        return self.root / collection_id

    def manifest_path(self, collection_id: str) -> Path:
        return self.collection_dir(collection_id) / "manifest.json"

    def project_dir(self, collection_id: str) -> Path:
        return self.collection_dir(collection_id) / "project"

    def jobs_dir(self, collection_id: str) -> Path:
        return self.collection_dir(collection_id) / "jobs"

    def collection_exists(self, collection_id: str) -> bool:
        return self.manifest_path(collection_id).exists()

    def load(self, collection_id: str) -> dict[str, Any]:
        path = self.manifest_path(collection_id)
        if not path.exists():
            raise NotFoundError(f"Collection not found: {collection_id}")
        manifest = read_json(path)
        manifest["source_count"] = file_count(self.project_dir(collection_id) / "input")
        manifest["updated_at"] = manifest.get("updated_at") or utc_now()
        return manifest

    def save(self, collection_id: str, manifest: dict[str, Any]) -> dict[str, Any]:
        manifest["id"] = collection_id
        manifest["project_path"] = "project"
        manifest["source_count"] = file_count(self.project_dir(collection_id) / "input")
        manifest["updated_at"] = utc_now()
        write_json(self.manifest_path(collection_id), manifest)
        return manifest

    def default_model_metadata(self) -> dict[str, Any]:
        return {
            "graphrag_version": self.env.get("GRAPHRAG_VERSION", "3.0.9"),
            "model_backend": self.env.get("GRAPHRAG_MODEL_BACKEND", "ollama"),
            "chat_model": self.env.get("GRAPHRAG_CHAT_MODEL", "unconfigured"),
            "embedding_model": self.env.get("GRAPHRAG_EMBED_MODEL", "unconfigured"),
            "embedding_dim": self.env.get("GRAPHRAG_EMBED_DIM", "unconfigured"),
        }

    def create_collection(
        self,
        name: str,
        description: str = "",
        collection_id: str | None = None,
    ) -> dict[str, Any]:
        collection_id = validate_collection_id(collection_id or collection_id_from_name(name))
        if self.collection_exists(collection_id):
            raise ConflictError(f"Collection already exists: {collection_id}")
        collection_dir = self.collection_dir(collection_id)
        (collection_dir / "project" / "input").mkdir(parents=True, exist_ok=True)
        (collection_dir / "jobs").mkdir(parents=True, exist_ok=True)
        manifest = {
            "schema_version": 1,
            "id": collection_id,
            "name": name,
            "description": description,
            "published": False,
            "status": "draft",
            "project_path": "project",
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "last_indexed_at": None,
            "model_metadata": self.default_model_metadata(),
            "source_count": 0,
            "available_query_methods": sorted(QUERY_METHODS),
            "last_job": None,
        }
        return self.save(collection_id, manifest)

    def list_collections(self, published_only: bool = True) -> list[dict[str, Any]]:
        manifests: list[dict[str, Any]] = []
        for manifest_path in sorted(self.root.glob("*/manifest.json")):
            try:
                manifest = self.load(manifest_path.parent.name)
            except GatewayError:
                continue
            if published_only and not manifest.get("published"):
                continue
            manifests.append(manifest)
        return manifests

    def find_collections(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        terms = [term for term in re.split(r"\W+", query.lower()) if term]
        scored: list[tuple[int, dict[str, Any]]] = []
        for manifest in self.list_collections(published_only=True):
            haystack = " ".join(
                [
                    manifest.get("id", ""),
                    manifest.get("name", ""),
                    manifest.get("description", ""),
                ]
            ).lower()
            score = sum(1 for term in terms if term in haystack)
            if score:
                scored.append((score, manifest))
        scored.sort(key=lambda item: (-item[0], item[1].get("name", "")))
        return [manifest for _, manifest in scored[: max(1, min(limit, 20))]]

    def add_document_bytes(
        self,
        collection_id: str,
        filename: str,
        content: bytes,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        manifest = self.load(collection_id)
        input_dir = self.project_dir(collection_id) / "input"
        input_dir.mkdir(parents=True, exist_ok=True)
        destination = input_dir / safe_filename(filename)
        if destination.exists() and not overwrite:
            raise ConflictError(f"Document already exists: {destination.name}")
        destination.write_bytes(content)
        if manifest.get("status") == "published":
            manifest["status"] = "stale"
        return self.save(collection_id, manifest)

    def validate_collection(self, collection_id: str) -> dict[str, Any]:
        manifest = self.load(collection_id)
        project_dir = self.project_dir(collection_id)
        input_dir = project_dir / "input"
        output_dir = project_dir / "output"
        errors: list[str] = []
        warnings: list[str] = []
        if not project_dir.exists():
            errors.append("Missing project directory.")
        if not input_dir.exists():
            errors.append("Missing project/input directory.")
        elif file_count(input_dir) == 0:
            warnings.append("No source documents are present under project/input.")
        if not (project_dir / "settings.yaml").exists():
            warnings.append("GraphRAG settings.yaml is missing; indexing will initialize the project.")
        if not output_dir.exists() or file_count(output_dir) == 0:
            warnings.append("No GraphRAG output artifacts were found yet.")
        return {
            "ok": not errors,
            "collection": manifest,
            "errors": errors,
            "warnings": warnings,
        }

    def publish_collection(self, collection_id: str, confirm: bool = False) -> dict[str, Any]:
        if not confirm:
            raise GatewayError("Publishing requires confirm=true.")
        validation = self.validate_collection(collection_id)
        manifest = validation["collection"]
        project_dir = self.project_dir(collection_id)
        if not (project_dir / "settings.yaml").exists():
            raise GatewayError("Cannot publish before GraphRAG settings.yaml exists.")
        output_dir = project_dir / "output"
        if not output_dir.exists() or file_count(output_dir) == 0:
            raise GatewayError("Cannot publish before GraphRAG output artifacts exist.")
        manifest["published"] = True
        manifest["status"] = "published"
        return self.save(collection_id, manifest)

    def unpublish_collection(self, collection_id: str, confirm: bool = False) -> dict[str, Any]:
        if not confirm:
            raise GatewayError("Unpublishing requires confirm=true.")
        manifest = self.load(collection_id)
        manifest["published"] = False
        manifest["status"] = "indexed" if manifest.get("last_indexed_at") else "draft"
        return self.save(collection_id, manifest)

    def import_pack(self, collection_id: str, archive_path: Path, overwrite: bool = False) -> dict[str, Any]:
        collection_id = validate_collection_id(collection_id)
        if not self.collection_exists(collection_id):
            self.create_collection(name=collection_id.replace("-", " ").title(), collection_id=collection_id)
        manifest = self.load(collection_id)
        collection_dir = self.collection_dir(collection_id)
        project_dir = self.project_dir(collection_id)
        with tempfile.TemporaryDirectory(prefix="graphrag-pack-") as tmp:
            tmp_path = Path(tmp)
            if zipfile.is_zipfile(archive_path):
                with zipfile.ZipFile(archive_path) as archive:
                    safe_extract_zip(archive, tmp_path)
            elif tarfile.is_tarfile(archive_path):
                with tarfile.open(archive_path) as archive:
                    safe_extract_tar(archive, tmp_path)
            else:
                raise GatewayError("Pack import supports .zip, .tar, .tar.gz, and .tgz archives.")
            source_project = find_project_root(tmp_path)
            if directory_has_files(project_dir):
                if not overwrite:
                    raise ConflictError("Collection project already contains files; use overwrite=true.")
                shutil.rmtree(project_dir)
            elif project_dir.exists():
                shutil.rmtree(project_dir)
            shutil.copytree(source_project, project_dir)
            pack_manifest_path = tmp_path / "manifest.json"
            if not pack_manifest_path.exists() and source_project.parent != tmp_path:
                pack_manifest_path = source_project.parent / "manifest.json"
            if pack_manifest_path.exists():
                try:
                    pack_manifest = read_json(pack_manifest_path)
                    manifest["name"] = pack_manifest.get("name", manifest["name"])
                    manifest["description"] = pack_manifest.get("description", manifest.get("description", ""))
                    manifest["model_metadata"] = pack_manifest.get(
                        "model_metadata", manifest.get("model_metadata", self.default_model_metadata())
                    )
                except (OSError, json.JSONDecodeError):
                    pass
        (collection_dir / "jobs").mkdir(parents=True, exist_ok=True)
        manifest["published"] = False
        manifest["status"] = "imported"
        manifest["last_job"] = None
        return self.save(collection_id, manifest)

    def export_pack(self, collection_id: str) -> Path:
        manifest = self.load(collection_id)
        exports_dir = self.collection_dir(collection_id) / "exports"
        exports_dir.mkdir(parents=True, exist_ok=True)
        timestamp = utc_now().replace(":", "").replace("-", "")
        archive_path = exports_dir / f"{collection_id}-{timestamp}.tar.gz"
        with tarfile.open(archive_path, "w:gz") as archive:
            manifest_tmp = tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False)
            try:
                json.dump(manifest, manifest_tmp, indent=2, sort_keys=True)
                manifest_tmp.write("\n")
                manifest_tmp.close()
                archive.add(manifest_tmp.name, arcname="manifest.json")
            finally:
                os.unlink(manifest_tmp.name)
            archive.add(self.project_dir(collection_id), arcname="project")
        return archive_path

    def create_job(self, collection_id: str, job_type: str, method: str) -> dict[str, Any]:
        self.load(collection_id)
        job_id = uuid.uuid4().hex[:12]
        log_path = self.jobs_dir(collection_id) / f"{job_id}.log"
        job = {
            "id": job_id,
            "collection_id": collection_id,
            "type": job_type,
            "method": method,
            "status": "queued",
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "started_at": None,
            "finished_at": None,
            "log_path": str(log_path),
            "error": None,
        }
        write_json(self.jobs_dir(collection_id) / f"{job_id}.json", job)
        return job

    def load_job(self, collection_id: str, job_id: str) -> dict[str, Any]:
        path = self.jobs_dir(collection_id) / f"{job_id}.json"
        if not path.exists():
            raise NotFoundError(f"Job not found: {job_id}")
        job = read_json(path)
        log_path = Path(job.get("log_path", ""))
        if log_path.exists():
            lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
            job["log_tail"] = lines[-80:]
        else:
            job["log_tail"] = []
        return job

    def save_job(self, collection_id: str, job: dict[str, Any]) -> dict[str, Any]:
        job["updated_at"] = utc_now()
        write_json(self.jobs_dir(collection_id) / f"{job['id']}.json", job)
        return job


class GraphRunner:
    def __init__(self, command: str = "graphrag-offnet", dry_run: bool = False):
        self.command = command
        self.dry_run = dry_run

    def env_for_project(self, project_dir: Path) -> dict[str, str]:
        env = os.environ.copy()
        env["PROJECT_ROOT"] = str(project_dir)
        env.setdefault("GRAPHRAG_MODEL_BACKEND", "ollama")
        return env

    def init_command(self) -> list[str]:
        return [self.command, "init"]

    def index_command(self, method: str) -> list[str]:
        if method not in INDEX_METHODS:
            raise GatewayError(f"Unsupported index method: {method}")
        return [self.command, "index", "--method", method, "--verbose"]

    def query_command(self, method: str, question: str) -> list[str]:
        if method not in QUERY_METHODS:
            raise GatewayError(f"Unsupported query method: {method}")
        if not question.strip():
            raise GatewayError("Question cannot be empty.")
        return [self.command, "query", "--method", method, question]

    def run(
        self,
        command: list[str],
        project_dir: Path,
        log_path: Path | None = None,
        timeout: int | None = None,
    ) -> subprocess.CompletedProcess[str]:
        if log_path:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(f"$ {' '.join(command)}\n")
        if self.dry_run:
            output = f"DRY RUN: {' '.join(command)}\n"
            if log_path:
                with log_path.open("a", encoding="utf-8") as handle:
                    handle.write(output)
            return subprocess.CompletedProcess(command, 0, output, "")
        result = subprocess.run(
            command,
            cwd=str(project_dir),
            env=self.env_for_project(project_dir),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        if log_path:
            with log_path.open("a", encoding="utf-8") as handle:
                if result.stdout:
                    handle.write(result.stdout)
                    if not result.stdout.endswith("\n"):
                        handle.write("\n")
                if result.stderr:
                    handle.write(result.stderr)
                    if not result.stderr.endswith("\n"):
                        handle.write("\n")
        if result.returncode != 0:
            raise GatewayError(
                f"Command failed with exit code {result.returncode}: {' '.join(command)}"
            )
        return result

    def ensure_initialized(self, project_dir: Path, log_path: Path | None = None) -> None:
        if (project_dir / "settings.yaml").exists():
            return
        self.run(self.init_command(), project_dir, log_path=log_path, timeout=900)

    def index(self, project_dir: Path, method: str, log_path: Path) -> None:
        self.ensure_initialized(project_dir, log_path=log_path)
        self.run(self.index_command(method), project_dir, log_path=log_path, timeout=None)

    def query(self, project_dir: Path, method: str, question: str, timeout: int = 900) -> str:
        result = self.run(self.query_command(method, question), project_dir, timeout=timeout)
        return result.stdout.strip()
