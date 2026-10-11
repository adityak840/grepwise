import hashlib

from grepwise.ingest.chunkers import Chunk
from grepwise.ingest.enrich import enrich


def test_method_header_matches_spec() -> None:
    chunk = Chunk("method", "Signer.sign", 10, 12, "def sign(self): ...\n", "class Signer")
    e = enrich("src/itsdangerous/signer.py", chunk)
    assert e.header == (
        "File: src/itsdangerous/signer.py\nSymbol: Signer.sign (method)\nInside: class Signer"
    )
    assert e.content_hash == hashlib.sha256(f"{e.header}\n{chunk.content}".encode()).hexdigest()
    assert e.embedding_text.startswith(e.header) and e.embedding_text.endswith(chunk.content)


def test_module_header_has_only_the_file() -> None:
    assert enrich("a.py", Chunk("module", None, 1, 1, "import os\n")).header == "File: a.py"


def test_same_content_in_another_file_hashes_differently() -> None:
    chunk = Chunk("module", None, 1, 1, "import os\n")
    assert enrich("a.py", chunk).content_hash != enrich("b.py", chunk).content_hash
