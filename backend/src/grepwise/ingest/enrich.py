"""Attach a context header and a content hash to each chunk."""

import hashlib
from dataclasses import dataclass

from grepwise.ingest.chunkers import Chunk


@dataclass(frozen=True)
class EnrichedChunk:
    chunk: Chunk
    header: str
    content_hash: str

    @property
    def embedding_text(self) -> str:
        """What gets embedded: the header, then the code."""
        return f"{self.header}\n\n{self.chunk.content}"


def make_header(path: str, chunk: Chunk) -> str:
    lines = [f"File: {path}"]
    if chunk.symbol:
        lines.append(f"Symbol: {chunk.symbol} ({chunk.kind})")
    if chunk.parent:
        lines.append(f"Inside: {chunk.parent}")
    return "\n".join(lines)


def content_hash(header: str, content: str) -> str:
    return hashlib.sha256(f"{header}\n{content}".encode()).hexdigest()


def enrich(path: str, chunk: Chunk) -> EnrichedChunk:
    header = make_header(path, chunk)
    return EnrichedChunk(chunk, header, content_hash(header, chunk.content))
