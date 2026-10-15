"""Turn demo notices into page-aware searchable chunks."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    source: str
    page: int | None
    title: str
    text: str
    kind: str = "notice"

    def as_dict(self) -> dict:
        return asdict(self)


def _split(text: str, size: int = 700) -> list[str]:
    words = text.split()
    return [" ".join(words[i : i + size]) for i in range(0, len(words), size)] or [""]


def ingest_notice(path: Path) -> list[Chunk]:
    suffix = path.suffix.lower()
    pages: list[tuple[int | None, str]] = []
    if suffix == ".pdf":
        try:
            import pdfplumber

            with pdfplumber.open(path) as pdf:
                pages = [(number, page.extract_text() or "") for number, page in enumerate(pdf.pages, 1)]
        except ImportError as exc:
            raise RuntimeError("pdfplumber is required to ingest PDF notices") from exc
    else:
        pages = [(None, path.read_text(encoding="utf-8"))]

    chunks: list[Chunk] = []
    title = path.stem.replace("_", " ").replace("-", " ").strip()
    for page, text in pages:
        for index, part in enumerate(_split(re.sub(r"\s+", " ", text).strip())):
            if not part:
                continue
            suffix_id = f"-p{page}-c{index}" if page else f"-c{index}"
            chunks.append(Chunk(f"{path.name}{suffix_id}", path.name, page, title, part))
    return chunks


def build_index(notices_dir: Path, output: Path) -> list[Chunk]:
    chunks = [chunk for path in sorted(notices_dir.iterdir()) if path.is_file() for chunk in ingest_notice(path)]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps([chunk.as_dict() for chunk in chunks], indent=2) + "\n")
    return chunks


def load_index(path: Path) -> list[Chunk]:
    if not path.exists():
        return []
    return [Chunk(**row) for row in json.loads(path.read_text())]


if __name__ == "__main__":
    from .settings import ROOT, Settings

    settings = Settings.from_env()
    chunks = build_index(ROOT / "data" / "notices", settings.index_path)
    print(f"Indexed {len(chunks)} notice chunks into {settings.index_path}")
