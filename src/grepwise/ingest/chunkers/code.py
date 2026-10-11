"""Tree-sitter splitter: one chunk per top-level function or class.

Rules (spec 01, section 6.3):
- top-level function            -> `function` chunk
- class within the size limit   -> one `class` chunk
- class over the size limit     -> a `method` chunk per method, plus `class` chunks for the
                                   signature, docstring and other class-level lines
- code between definitions      -> contiguous runs grouped into `module` chunks
- oversized function / method   -> `window` chunks overlapping by ~15% of their lines

Comments directly above a definition (no blank line between) belong to that definition.
Line ranges come from tree-sitter rows, which count '\\n' exactly like `split_lines`.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import PurePosixPath

import tree_sitter_language_pack as tslp
from tree_sitter import Node, Parser

from grepwise.ingest.chunkers import (
    MAX_CHUNK_CHARS,
    Chunk,
    make_chunk,
    range_chars,
    size_windows,
    split_lines,
    trim_blank,
)
from grepwise.ingest.chunkers.fallback import chunk_fallback

log = logging.getLogger(__name__)

LANGUAGE_BY_EXTENSION = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".tsx": "tsx",
}

_JS_FUNCTION_VALUES = {"arrow_function", "function_expression", "function", "generator_function"}
_JS_CLASS_VALUES = {"class"}


def detect_language(path: str) -> str | None:
    return LANGUAGE_BY_EXTENSION.get(PurePosixPath(path).suffix.lower())


GRAMMARS = ["python", "javascript", "typescript", "tsx"]


def ensure_grammar(language: str) -> None:
    """Download a grammar the first time it's needed (a fresh install hasn't run setup),
    instead of letting chunking fall back to plain windows without a word."""
    if language not in tslp.downloaded_languages():
        log.info("Downloading the tree-sitter grammar for %s", language)
        tslp.download([language])


@lru_cache
def _parser(language: str) -> Parser:
    ensure_grammar(language)
    return Parser(tslp.get_language(language))


# --- Recognising definitions -------------------------------------------------------------


@dataclass(frozen=True)
class Definition:
    kind: str  # 'function' or 'class'
    name: str
    node: Node  # the function/class node itself (inside any decorator/export wrapper)


# Classifiers get the source bytes explicitly. We never use `Node.text`: in tree-sitter
# 0.26 it reads the tree's source buffer, and segfaults if those bytes have been freed.
type Classifier = Callable[[Node, bytes], "Definition | None"]


def _name(node: Node | None, source: bytes, default: str = "default") -> str:
    if node is None:
        return default
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _classify_python(node: Node, source: bytes) -> Definition | None:
    if node.type == "decorated_definition":
        inner = node.child_by_field_name("definition")
        return _classify_python(inner, source) if inner else None
    if node.type == "function_definition":
        return Definition("function", _name(node.child_by_field_name("name"), source), node)
    if node.type == "class_definition":
        return Definition("class", _name(node.child_by_field_name("name"), source), node)
    return None


def _classify_js(node: Node, source: bytes) -> Definition | None:
    t = node.type
    if t == "export_statement":
        inner = node.child_by_field_name("declaration") or node.child_by_field_name("value")
        return _classify_js(inner, source) if inner else None
    if t in ("function_declaration", "generator_function_declaration"):
        return Definition("function", _name(node.child_by_field_name("name"), source), node)
    if t in ("function_expression", "function", "generator_function", "arrow_function"):
        # `export default function () {}` and similar anonymous forms
        return Definition("function", _name(node.child_by_field_name("name"), source), node)
    if t in ("class_declaration", "abstract_class_declaration", "class"):
        return Definition("class", _name(node.child_by_field_name("name"), source), node)
    if t in ("lexical_declaration", "variable_declaration"):
        # `const handler = () => {...}` / `const Foo = class {...}`, single declarator only
        declarators = [c for c in node.named_children if c.type == "variable_declarator"]
        if len(declarators) != 1:
            return None
        value = declarators[0].child_by_field_name("value")
        name = _name(declarators[0].child_by_field_name("name"), source)
        if value is not None and value.type in _JS_FUNCTION_VALUES:
            return Definition("function", name, value)
        if value is not None and value.type in _JS_CLASS_VALUES:
            return Definition("class", name, value)
    return None


def _classify_js_member(node: Node, source: bytes) -> Definition | None:
    if node.type == "method_definition":
        return Definition("function", _name(node.child_by_field_name("name"), source), node)
    return None


# --- Turning sibling nodes into line-ranged items ----------------------------------------


@dataclass
class Item:
    start: int  # 1-based, inclusive
    end: int
    definition: Definition | None  # None for other code (imports, statements, comments)
    is_comment: bool = False
    absorbed: bool = False  # a comment that now belongs to the definition below it


def _line_range(node: Node) -> tuple[int, int]:
    start_row, end_row = node.start_point.row, node.end_point.row
    if node.end_point.column == 0 and end_row > start_row:
        end_row -= 1  # node ends with a newline; its last real line is the one before
    return start_row + 1, end_row + 1


def _items(nodes: list[Node], lines: list[str], source: bytes, classify: Classifier) -> list[Item]:
    items: list[Item] = []
    for node in nodes:
        start, end = _line_range(node)
        is_comment = node.type == "comment"
        # Only a comment that starts its own line can attach to the definition below it.
        own_line = not lines[start - 1][: node.start_point.column].strip()
        items.append(Item(start, end, classify(node, source), is_comment and own_line))

    for i, item in enumerate(items):
        if item.definition is None:
            continue
        j = i - 1
        while j >= 0 and items[j].is_comment and items[j].end == item.start - 1:
            items[j].absorbed = True
            item.start = items[j].start
            j -= 1
    return [it for it in items if not it.absorbed]


# --- Emitting chunks ---------------------------------------------------------------------


class _Splitter:
    def __init__(
        self,
        lines: list[str],
        source: bytes,
        classify_top: Classifier,
        classify_member: Classifier,
    ):
        self.lines = lines
        self.source = source
        self.classify_top = classify_top
        self.classify_member = classify_member
        self.chunks: list[Chunk] = []

    def fits(self, start: int, end: int) -> bool:
        return range_chars(self.lines, start, end) <= MAX_CHUNK_CHARS

    def emit_run(
        self, run: list[Item], kind: str, symbol: str | None, parent: str | None = None
    ) -> None:
        """Group a contiguous run of non-definition items into chunks of `kind`."""
        if not run:
            return
        trimmed = trim_blank(self.lines, run[0].start, run[-1].end)
        if trimmed is None:
            return
        for s, e in size_windows(self.lines, *trimmed):
            self.chunks.append(make_chunk(self.lines, kind, symbol, s, e, parent))
        run.clear()

    def emit_function(self, item: Item, kind: str, symbol: str, parent: str | None) -> None:
        if self.fits(item.start, item.end):
            self.chunks.append(make_chunk(self.lines, kind, symbol, item.start, item.end, parent))
            return
        for s, e in size_windows(self.lines, item.start, item.end):
            self.chunks.append(make_chunk(self.lines, "window", symbol, s, e, parent))

    def emit_class(self, item: Item, symbol: str, parent: str | None) -> None:
        if self.fits(item.start, item.end):
            self.chunks.append(
                make_chunk(self.lines, "class", symbol, item.start, item.end, parent)
            )
            return

        assert item.definition is not None
        body = item.definition.node.child_by_field_name("body")
        children = body.named_children if body else []
        members = _items(children, self.lines, self.source, self.classify_member)
        defs = [m for m in members if m.definition is not None]
        if not defs:  # a huge class with no methods, e.g. a big table of constants
            for s, e in size_windows(self.lines, item.start, item.end):
                self.chunks.append(make_chunk(self.lines, "window", symbol, s, e, parent))
            return

        # Class signature + docstring + anything before the first method.
        header = trim_blank(self.lines, item.start, defs[0].start - 1)
        if header:
            for s, e in size_windows(self.lines, *header):
                self.chunks.append(make_chunk(self.lines, "class", symbol, s, e, parent))

        inside = f"class {symbol}"
        run: list[Item] = []
        for member in members:
            if member.start < defs[0].start:
                continue  # already in the header chunk
            d = member.definition
            if d is None:
                run.append(member)
                continue
            self.emit_run(run, "class", symbol, parent)  # class-level lines between methods
            if d.kind == "class":
                self.emit_class(member, f"{symbol}.{d.name}", inside)
            else:
                self.emit_function(member, "method", f"{symbol}.{d.name}", inside)
        self.emit_run(run, "class", symbol, parent)

    def split_top_level(self, nodes: list[Node]) -> list[Chunk]:
        run: list[Item] = []
        for item in _items(nodes, self.lines, self.source, self.classify_top):
            d = item.definition
            if d is None:
                run.append(item)
                continue
            self.emit_run(run, "module", None)
            if d.kind == "class":
                self.emit_class(item, d.name, None)
            else:
                self.emit_function(item, "function", d.name, None)
        self.emit_run(run, "module", None)
        return sorted(self.chunks, key=lambda c: (c.start_line, c.end_line))


def chunk_code(text: str, language: str) -> list[Chunk]:
    """Split source code in a supported language; falls back to line windows on failure."""
    lines = split_lines(text)
    if not any(line.strip() for line in lines):
        return []
    try:
        source = text.encode("utf-8")
        tree = _parser(language).parse(source)
    except Exception:  # grammar missing (offline, first run) or parser crash
        log.warning("tree-sitter unavailable for %s; using line windows", language, exc_info=True)
        return chunk_fallback(text)
    root = tree.root_node
    if root.has_error:
        return chunk_fallback(text)

    if language == "python":
        # Python methods are plain function_definitions inside the class body.
        splitter = _Splitter(lines, source, _classify_python, _classify_python)
    else:
        splitter = _Splitter(lines, source, _classify_js, _classify_js_member)
    return splitter.split_top_level(root.named_children)


def chunk_file(path: str, text: str) -> tuple[str | None, list[Chunk]]:
    """Return (language, chunks) for one file. Language is None when unsupported."""
    language = detect_language(path)
    if language is None:
        return None, chunk_fallback(text)
    return language, chunk_code(text, language)
