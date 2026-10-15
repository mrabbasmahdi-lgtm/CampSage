"""Cheap local retrieval for a small campus notice corpus."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from .ingest import Chunk


@dataclass(frozen=True)
class ContextChunk:
    source_id: str
    source: str
    page: int | None
    text: str
    kind: str

    def as_dict(self) -> dict:
        return asdict(self)


def _words(text: str) -> set[str]:
    return {word for word in re.split(r"[^\w]+", text.lower()) if len(word) > 1}


def _score(query: set[str], chunk: Chunk) -> int:
    title = _words(chunk.title + " " + chunk.source)
    body = _words(chunk.text)
    return sum((10 if word in title else 0) + (3 if word in body else 0) for word in query)


def student_record_chunks(records: dict) -> list[ContextChunk]:
    return [
        ContextChunk(f"student-record-{key}", "Your student record", None, f"{key}: {value}", "student_record")
        for key, value in records.items()
    ]


def retrieve(question: str, profile: dict, student_records: dict, chunks: list[Chunk], limit: int = 5) -> list[ContextChunk]:
    del profile
    query = _words(question)
    ranked = sorted(((score, chunk) for chunk in chunks if (score := _score(query, chunk)) > 0), reverse=True, key=lambda pair: pair[0])
    public = [ContextChunk(chunk.chunk_id, chunk.source, chunk.page, chunk.text, chunk.kind) for _, chunk in ranked[:limit]]
    private = student_record_chunks(student_records)
    return public + private
