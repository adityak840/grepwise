"""Extra keyword-search words from splitting code identifiers.

Postgres full-text search sees `refreshToken` as one word, so a question about
"refresh token" would miss it. We append the split-up words to each chunk's
`search_text`, and split query words the same way.
"""

import re

# A run of letters/digits/underscores that starts with a letter or underscore.
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# Within one identifier: acronyms (HTTP in HTTPServer), Capitalised or lower words, digit runs.
_PART = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")


def split_identifier(identifier: str) -> list[str]:
    """`HTTPServerError` -> ['http', 'server', 'error']; `sha256` -> ['sha', '256']."""
    return [p.lower() for p in _PART.findall(identifier)]


def identifier_words(text: str) -> list[str]:
    """Words from every identifier in `text` that splits into more than one part, deduplicated
    in first-seen order."""
    seen: dict[str, None] = {}
    for ident in _IDENTIFIER.findall(text):
        parts = split_identifier(ident)
        if len(parts) > 1:
            seen.update(dict.fromkeys(parts))
    return list(seen)


def search_text(symbol: str | None, content: str) -> str:
    """What keyword search indexes: symbol, content, then the split identifier words."""
    base = f"{symbol or ''} {content}"
    extra = identifier_words(base)
    return f"{base}\n{' '.join(extra)}" if extra else base


def query_terms(query: str, max_terms: int = 40) -> list[str]:
    """Plain alphanumeric terms for an OR keyword query: the words as typed, plus their split
    parts. Only letters and digits survive, so nothing can inject tsquery syntax."""
    terms: dict[str, None] = {}
    for word in re.findall(r"[^\W_]+(?:_[^\W_]+)*", query):
        for token in [*re.findall(r"[^\W_]+", word), *split_identifier(word)]:
            terms[token.lower()] = None
    return list(terms)[:max_terms]
