"""Clickable links to cited lines (spec 13, section 5)."""

from urllib.parse import quote, unquote, urlparse


def link_for(
    url: str, ref: str | None, version: str, path: str, start: int, end: int, source_type: str
) -> str | None:
    """GitHub: .../blob/<ref>/<path>#L<start>-L<end>. A local folder: /abs/path:<start>,
    which terminals open on click. API endpoints have no file to link to."""
    if source_type == "openapi":
        return None
    if url.startswith("https://github.com/"):
        lines = f"#L{start}" if start == end else f"#L{start}-L{end}"
        # Sources indexed before N2 have no ref; for them the version is the tag.
        return f"{url.rstrip('/')}/blob/{quote(ref or version)}/{quote(path)}{lines}"
    if url.startswith("file://"):
        folder = unquote(urlparse(url).path).rstrip("/")
        return f"{folder}/{path}:{start}"
    return None
