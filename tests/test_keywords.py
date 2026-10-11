import pytest

from grepwise.ingest.keywords import identifier_words, query_terms, search_text, split_identifier


@pytest.mark.parametrize(
    ("identifier", "parts"),
    [
        ("refreshToken", ["refresh", "token"]),
        ("handle_refresh_token", ["handle", "refresh", "token"]),
        ("HTTPServerError", ["http", "server", "error"]),
        ("parseHTTPResponse", ["parse", "http", "response"]),
        ("sha256", ["sha", "256"]),
        ("base64Encode", ["base", "64", "encode"]),
        ("utf8_decode", ["utf", "8", "decode"]),
        ("URL", ["url"]),
        ("__init__", ["init"]),
        ("TimestampSigner", ["timestamp", "signer"]),
    ],
)
def test_split_identifier(identifier: str, parts: list[str]) -> None:
    assert split_identifier(identifier) == parts


def test_identifier_words_skips_plain_words_and_dedupes() -> None:
    text = "def refreshToken(token): return refresh_token or plain"
    assert identifier_words(text) == ["refresh", "token"]


def test_search_text_appends_split_words() -> None:
    assert search_text("Signer.get_signature", "x = 1") == (
        "Signer.get_signature x = 1\nget signature"
    )
    assert search_text(None, "plain words only") == " plain words only"


def test_query_terms_are_plain_alphanumerics() -> None:
    assert query_terms("How do I use refreshToken?") == [
        "how", "do", "i", "use", "refreshtoken", "refresh", "token",
    ]  # fmt: skip
    # tsquery syntax characters never survive
    assert query_terms("a & b | !c <-> 'd':*") == ["a", "b", "c", "d"]
    assert query_terms("&&& |||") == []
