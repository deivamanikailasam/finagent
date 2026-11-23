"""Corpus ingestion. Corpus text is data: it is hashed and parsed, never executed or obeyed."""

import hashlib
import json
import os
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

from finagent.config.clock import local_date
from finagent.domain.enums import TrustClass
from finagent.rag.registry import RegistryEntry, load_registry
from finagent.rag.section_hash import document_body

_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---(?:\n|\Z)", re.DOTALL)
_TITLE = re.compile(r"^# (.+)$", re.MULTILINE)
UNREGISTERED_PREFIX = "UNREGISTERED:"


class IndexIntegrityError(RuntimeError):
    """The persisted index manifest is missing or does not match the corpus."""


@dataclass(frozen=True)
class Finding:
    code: str
    file: str
    doc_id: str | None = None
    detail: str = ""


@dataclass(frozen=True)
class Document:
    doc_id: str
    file: str
    version: str
    title: str
    trust_class: TrustClass
    effective: date | None
    superseded: date | None
    acl: tuple[str, ...]
    restricted: bool
    sha256: str
    text: str


@dataclass(frozen=True)
class IngestResult:
    documents: tuple[Document, ...]
    findings: tuple[Finding, ...]
    index_hash: str
    registry_sha256: str = ""

    def document(self, doc_id: str) -> Document | None:
        return next((item for item in self.documents if item.doc_id == doc_id), None)

    @property
    def has_integrity_findings(self) -> bool:
        """True when a registered document is missing, edited or unreadable."""
        return any(finding.doc_id is not None for finding in self.findings)


def _corpus_files(corpus_dir: Path) -> Iterator[tuple[Path, str]]:
    for path in sorted(corpus_dir.rglob("*")):
        relative = path.relative_to(corpus_dir)
        if any(part == "__MACOSX" or part.startswith(".") for part in relative.parts):
            continue
        if path.suffix == ".md" and (path.is_file() or path.is_symlink()):
            yield path, relative.as_posix()


def _frontmatter(text: str) -> dict[str, Any] | None:
    """Return parsed frontmatter, {} when absent, or None when it cannot be parsed."""
    match = _FRONTMATTER.match(text)
    if match is None:
        return {}
    try:
        parsed = yaml.safe_load(match.group(1))
    except (yaml.YAMLError, ValueError, RecursionError):  # bad dates, huge integers, deep nesting
        return None
    return parsed if isinstance(parsed, dict) else None


def _scalar_text(value: Any) -> str | None:
    if value is None:
        return None
    # Never stringify a container: a hostile YAML alias could expand it enormously.
    return str(value) if isinstance(value, str | int | float | date) else "<non-scalar>"


def _frontmatter_findings(meta: dict[str, Any], entry: RegistryEntry) -> list[Finding]:
    # Frontmatter never confers trust, so a disagreement is reported and the registry still wins.
    checks = [
        ("document_id", entry.doc_id),
        ("version", entry.version),
        ("superseded_date", entry.superseded),
    ]
    if entry.effective is not None:  # untrusted documents have no registered effective date to compare
        checks.append(("effective_date", entry.effective))
    findings = []
    for key, expected in checks:
        if _scalar_text(meta.get(key)) != _scalar_text(expected):
            findings.append(Finding("frontmatter_mismatch", entry.file, entry.doc_id, key))
    return findings


def _title(body: str, relative: str) -> str:
    match = _TITLE.search(body)
    return match.group(1).strip() if match else Path(relative).stem


def _build_document(relative: str, text: str, digest: str, entry: RegistryEntry | None) -> Document:
    body = document_body(text)
    if entry is None:
        # The ID comes from the path, so a file cannot claim a real document ID through its frontmatter.
        return Document(
            doc_id=f"{UNREGISTERED_PREFIX}{relative}",
            file=relative,
            version="unregistered",
            title=_title(body, relative),
            trust_class=TrustClass.EXTERNAL_UNVERIFIED,
            effective=None,
            superseded=None,
            acl=("all",),
            restricted=False,
            sha256=digest,
            text=text,
        )
    unchanged = digest == entry.sha256
    return Document(
        doc_id=entry.doc_id,
        file=relative,
        version=entry.version if unchanged else f"modified:{digest[:8]}",  # never the approved version
        title=_title(body, relative),
        trust_class=entry.trust_class if unchanged else TrustClass.EXTERNAL_UNVERIFIED,
        effective=entry.effective,
        superseded=entry.superseded,
        acl=entry.acl,  # an edited restricted file stays restricted
        restricted=entry.restricted,
        sha256=digest,
        text=text,
    )


def _index_hash(documents: list[Document]) -> str:
    identity = [[doc.file, doc.doc_id, doc.sha256, str(doc.trust_class)] for doc in documents]
    return hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()


def ingest(corpus_dir: Path, registry_path: Path) -> IngestResult:
    registry = load_registry(registry_path)
    documents: list[Document] = []
    findings: list[Finding] = []
    seen: set[str] = set()
    for path, relative in _corpus_files(corpus_dir):
        entry = registry.get(relative)
        seen.add(relative)
        doc_id = entry.doc_id if entry else None
        if path.is_symlink():
            findings.append(Finding("symlink_skipped", relative, doc_id))  # could point outside the corpus
            continue
        try:
            text = path.read_bytes().decode("utf-8").replace("\r\n", "\n")
        except UnicodeDecodeError:
            findings.append(Finding("not_utf8", relative, doc_id))
            continue
        except OSError:
            findings.append(Finding("unreadable_file", relative, doc_id))
            continue
        digest = hashlib.sha256(text.encode()).hexdigest()
        meta = _frontmatter(text)
        if meta is None:
            findings.append(Finding("frontmatter_unparseable", relative, doc_id))
        if entry is None:
            findings.append(Finding("unregistered_file", relative))
        else:
            if digest != entry.sha256:
                findings.append(Finding("hash_mismatch", relative, entry.doc_id))
            findings.extend(_frontmatter_findings(meta or {}, entry))
        documents.append(_build_document(relative, text, digest, entry))
    for missing in sorted(set(registry) - seen):
        findings.append(Finding("registered_file_missing", missing, registry[missing].doc_id))
    registry_digest = hashlib.sha256(registry_path.read_bytes()).hexdigest()
    return IngestResult(tuple(documents), tuple(findings), _index_hash(documents), registry_digest)


def is_authoritative(result: IngestResult, doc_id: str, as_of: datetime) -> bool:
    """True when the document is registered, unmodified and valid on the Sydney date of as_of."""
    document = result.document(doc_id)
    if document is None or document.trust_class != TrustClass.AUTHORITATIVE or document.effective is None:
        return False
    day = local_date(as_of)
    return document.effective <= day and (document.superseded is None or day < document.superseded)


def _manifest(result: IngestResult) -> dict[str, Any]:
    return {
        "index_hash": result.index_hash,
        "registry_sha256": result.registry_sha256,
        "documents": [
            {
                "doc_id": doc.doc_id,
                "file": doc.file,
                "version": doc.version,
                "sha256": doc.sha256,
                "trust_class": str(doc.trust_class),
                "effective": doc.effective.isoformat() if doc.effective else None,
                "superseded": doc.superseded.isoformat() if doc.superseded else None,
                "acl": list(doc.acl),
                "restricted": doc.restricted,
            }
            for doc in result.documents
        ],
    }


def persist_manifest(result: IngestResult, path: Path) -> None:
    if result.has_integrity_findings:
        # Recording a damaged corpus as the baseline would make the damage verify as clean.
        raise IndexIntegrityError("refusing to record a manifest for a corpus with integrity findings")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(_manifest(result), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)  # a crash cannot leave a half-written manifest


def verify_index(result: IngestResult, path: Path) -> None:
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise IndexIntegrityError("index manifest is missing or unreadable") from exc
    if stored != _manifest(result):
        raise IndexIntegrityError("index manifest does not match the corpus")


def load_verified(corpus_dir: Path, registry_path: Path, manifest_path: Path) -> IngestResult:
    """Ingest and fail closed unless the corpus and registry match the recorded manifest."""
    result = ingest(corpus_dir, registry_path)
    if not manifest_path.exists():
        persist_manifest(result, manifest_path)
    verify_index(result, manifest_path)
    return result


def _printable(text: str) -> str:
    # A file name containing a newline must not be able to forge a row in the report.
    return text.encode("unicode_escape").decode("ascii")


def trust_report(result: IngestResult) -> str:
    rows = [
        f"{_printable(doc.doc_id):<18} {doc.trust_class:<20} {doc.version:<12} {_printable(doc.file)}"
        for doc in result.documents
    ]
    notes = [
        f"finding: {item.code} {_printable(item.file)} {item.detail}".rstrip() for item in result.findings
    ]
    return "\n".join([*rows, *notes, f"documents: {len(result.documents)}  findings: {len(result.findings)}"])


def main() -> None:
    result = ingest(Path("corpus"), Path("config/registry.yaml"))
    print(trust_report(result))
    try:
        persist_manifest(result, Path(os.environ.get("FINAGENT_HOME") or "var") / "index_manifest.json")
    except IndexIntegrityError as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()
