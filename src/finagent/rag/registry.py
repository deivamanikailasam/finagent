"""The document registry: the only source of trust for corpus files (decision log II)."""

from datetime import date
from pathlib import Path

import yaml
from pydantic import Field

from finagent.domain.enums import TrustClass
from finagent.domain.types import DomainModel, Sha256Hex


class RegistryEntry(DomainModel):
    doc_id: str = Field(min_length=1)
    file: str = Field(min_length=1)
    version: str = Field(min_length=1)
    trust_class: TrustClass
    effective: date | None = None
    superseded: date | None = None
    acl: tuple[str, ...] = Field(min_length=1)
    sha256: Sha256Hex
    restricted: bool = False
    audit_event: str | None = None


def load_registry(path: Path) -> dict[str, RegistryEntry]:
    """Return registry entries keyed by file name. Duplicate files or IDs are a configuration error."""
    with path.open(encoding="utf-8") as handle:
        entries = [RegistryEntry.model_validate(item) for item in yaml.safe_load(handle)["documents"]]
    by_file = {entry.file: entry for entry in entries}
    if len(by_file) != len(entries) or len({entry.doc_id for entry in entries}) != len(entries):
        raise ValueError("registry has a duplicate file or doc_id")
    return by_file
