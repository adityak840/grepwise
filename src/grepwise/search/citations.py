"""Citations: `<name>@<version>:<path>:L<start>-L<end>`.

Names and versions never contain '@' or ':' (validated at index time), but paths can
(OpenAPI virtual paths like 'GET /pet/{petId}'), so parsing anchors on both ends.
"""

import re
from dataclasses import dataclass

_CITATION = re.compile(
    r"^(?P<name>[^@:]+)@(?P<version>[^@:]+):(?P<path>.+):L(?P<start>\d+)-L(?P<end>\d+)$"
)


class CitationError(ValueError):
    pass


@dataclass(frozen=True)
class Citation:
    name: str
    version: str
    path: str
    start_line: int
    end_line: int

    @property
    def source(self) -> str:
        return f"{self.name}@{self.version}"

    def __str__(self) -> str:
        return format_citation(self.name, self.version, self.path, self.start_line, self.end_line)


def format_citation(name: str, version: str, path: str, start_line: int, end_line: int) -> str:
    return f"{name}@{version}:{path}:L{start_line}-L{end_line}"


def parse_citation(text: str) -> Citation:
    match = _CITATION.match(text.strip())
    if not match:
        raise CitationError(f"Not a citation: {text!r}")
    start, end = int(match["start"]), int(match["end"])
    if not 1 <= start <= end:
        raise CitationError(f"Bad line range in citation: {text!r}")
    return Citation(match["name"], match["version"], match["path"], start, end)
