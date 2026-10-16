"""Cheap local retrieval for a small campus notice corpus."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from .ingest import Chunk


_STOP_WORDS = {
    "a", "an", "and", "are", "about", "do", "does", "for", "how", "i",
    "in", "is", "it", "me", "my", "of", "on", "or", "the", "to", "what",
    "when", "where", "which", "who", "with", "you", "your",
}


@dataclass(frozen=True)
class ContextChunk:
    source_id: str
    source: str
    page: int | None
    text: str
    kind: str

    def as_dict(self) -> dict:
        return asdict(self)


def stem(word: str) -> str:
    """Very light English stemmer so "exams" matches "exam" and "fees" matches "fee"."""

    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 5 and word.endswith("ing"):
        return word[:-3]
    if len(word) > 4 and word.endswith("es") and not word.endswith(("ses", "xes")):
        return word[:-1]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _words(text: str) -> set[str]:
    return {
        stem(word)
        for word in re.split(r"[^\w]+", text.lower())
        if len(word) > 1 and word not in _STOP_WORDS
    }


def _score(query: set[str], chunk: Chunk) -> int:
    title = _words(chunk.title + " " + chunk.source)
    body = _words(chunk.text)
    return sum((10 if word in title else 0) + (3 if word in body else 0) for word in query)


def student_record_chunks(records: dict, profile: dict | None = None) -> list[ContextChunk]:
    chunks = [
        ContextChunk(f"student-record-{key}", "Your student record", None, f"{key}: {value}", "student_record")
        for key, value in records.items()
    ]
    basics = {key: profile[key] for key in ("semester", "class", "branch") if profile and profile.get(key)}
    if basics:
        text = "; ".join(f"{key}: {value}" for key, value in basics.items())
        chunks.append(ContextChunk("student-record-profile", "Your student record", None, text, "student_record"))
    return chunks


def retrieve(question: str, profile: dict, student_records: dict, chunks: list[Chunk], limit: int = 5) -> list[ContextChunk]:
    query = _words(question)
    ranked = sorted(((score, chunk) for chunk in chunks if (score := _score(query, chunk)) > 0), reverse=True, key=lambda pair: pair[0])
    public = [ContextChunk(chunk.chunk_id, chunk.source, chunk.page, chunk.text, chunk.kind) for _, chunk in ranked[:limit]]
    private = student_record_chunks(student_records, profile)
    return public + private
