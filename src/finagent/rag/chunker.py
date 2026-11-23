"""One chunk per numbered section, no overlap (decision log IX)."""

import re
from dataclasses import dataclass

from finagent.domain.enums import TrustClass
from finagent.rag.loader import Document
from finagent.rag.section_hash import document_body, section_hash

_H2 = re.compile(r"^## (.*)$", re.MULTILINE)
_NUMBERED = re.compile(r"([1-9][0-9]{0,3})\.\s+\S")  # the same section numbers section_hash can locate
_REFERENCE = re.compile(r"(?<![\w-])(?:FIN-POL-[0-9]{3}(?:-OLD)?|ADV-[0-9]{3})(?![\w-])")
_TITLE_LINE = re.compile(r"\A\s*# [^\n]*\n?")


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    doc_id: str
    section: str
    heading: str
    text: str
    prefixed_text: str
    refs: tuple[str, ...]
    section_hash: str
    trust_class: TrustClass
    version: str
    acl: tuple[str, ...]
    restricted: bool


def _chunk(document: Document, number: int, heading: str, text: str) -> Chunk:
    references = sorted(set(_REFERENCE.findall(text)) - {document.doc_id})
    return Chunk(
        chunk_id=f"{document.doc_id}#s{number}",
        doc_id=document.doc_id,
        section=f"s{number}",
        heading=heading,
        text=text,
        prefixed_text=f"{document.title} › {heading}\n{text}",
        refs=tuple(references),
        section_hash=section_hash(document.text, number),
        trust_class=document.trust_class,
        version=document.version,
        acl=document.acl,
        restricted=document.restricted,
    )


def chunk_document(document: Document) -> list[Chunk]:
    body = document_body(document.text)
    headings = list(_H2.finditer(body))
    numbers = [_NUMBERED.match(match.group(1)) for match in headings]
    values = [int(number.group(1)) if number else None for number in numbers]
    preamble = _TITLE_LINE.sub("", body[: headings[0].start()]).strip() if headings else ""
    # Irregular files get one whole chunk, so no text is lost and no chunk ID repeats.
    if not headings or preamble or None in values or len(set(values)) != len(values):
        return [_chunk(document, 0, "Document", body.strip())]
    chunks = []
    for index, match in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(body)
        chunks.append(
            _chunk(document, values[index], match.group(1).strip(), body[match.start() : end].strip())
        )
    return chunks


def chunk_documents(documents: tuple[Document, ...] | list[Document]) -> list[Chunk]:
    return [chunk for document in documents for chunk in chunk_document(document)]
