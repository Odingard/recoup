from __future__ import annotations

from dataclasses import dataclass

from .pages import Page


@dataclass(frozen=True)
class Chunk:
    pages: list[Page]
    start: int
    end: int

    @property
    def text(self) -> str:
        return "\n\n".join(f"[[PAGE {page.number}]]\n{page.text}" for page in self.pages)


def chunk_pages(pages: list[Page], max_chars: int = 60000, overlap_pages: int = 1) -> list[Chunk]:
    if not pages:
        return []
    chunks: list[Chunk] = []
    index = 0
    while index < len(pages):
        end = index
        chars = 0
        while end < len(pages):
            page_chars = len(pages[end].text) + 14
            if end > index and chars + page_chars > max_chars:
                break
            chars += page_chars
            end += 1
        if end == index:
            end += 1
        selected = pages[index:end]
        chunks.append(Chunk(selected, selected[0].number, selected[-1].number))
        if end >= len(pages):
            break
        index = max(index + 1, end - max(0, overlap_pages))
    return chunks
