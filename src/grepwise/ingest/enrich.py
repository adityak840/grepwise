"""Attach a context header, a content hash and keyword-search text to each chunk."""

import hashlib
from dataclasses import dataclass

from grepwise.ingest.chunkers import Chunk
from grepwise.ingest.keywords import search_text


@dataclass(frozen=True)
class EnrichedChunk:
    chunk: Chunk
    header: str
    content_hash: str
    search_text: str

    @property
    def embedding_text(self) -> str:
        """What gets embedded: the header, then the content."""
        return f"{self.header}\n\n{self.chunk.content}"


def make_header(path: str, chunk: Chunk) -> str:
    """Header for code chunks."""
    lines = [f"File: {path}"]
    if chunk.symbol:
        lines.append(f"Symbol: {chunk.symbol} ({chunk.kind})")
    if chunk.parent:
        lines.append(f"Inside: {chunk.parent}")
    return "\n".join(lines)


# Docs and API headers name the source but not its version: the header is embedded and
# hashed, so including the version would force re-embedding every chunk on each new version.


def docs_header(name: str, path: str, chunk: Chunk) -> str:
    """Header for docs sections; `name` is the source name, without a version."""
    return f"Docs: {name}\nFile: {path}\nSection: {chunk.symbol}"


def endpoint_header(name: str, chunk: Chunk) -> str:
    """Header for OpenAPI endpoints; `name` is the source name, without a version."""
    return f"API: {name}\nEndpoint: {chunk.symbol}"


def content_hash(header: str, content: str) -> str:
    return hashlib.sha256(f"{header}\n{content}".encode()).hexdigest()


def enrich(path: str, chunk: Chunk, header: str | None = None) -> EnrichedChunk:
    """`header` defaults to the code header."""
    header = header if header is not None else make_header(path, chunk)
    return EnrichedChunk(
        chunk,
        header,
        content_hash(header, chunk.content),
        search_text(chunk.symbol, chunk.content),
    )
