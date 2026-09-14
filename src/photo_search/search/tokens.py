"""Deterministic tokenisation and controlled MATCH construction (P2-04).

Architecture §4.3/§9.4 fix the shape: the FTS copy holds the *normalised search text*
("统一分词后的检索文本"), every token is quoted and escaped before it reaches MATCH, and
the AND/prefix/phrase policy is fixed and testable. This module is that policy, version
``TOKENIZER_VERSION``. It uses only the standard library - no ``jieba`` and no other new
dependency - and it never lets user input become FTS5 syntax.

Policy of version 1
-------------------

* Normalisation: NFKC (full-width becomes half-width) then case folding, applied twice so
  the result is both folded and NFKC-stable.
* A *token character* is what SQLite's ``unicode61`` tokenizer treats as one
  (``L*``/``N*``/``Co``); everything else separates tokens. ``_``, ``.``, ``-``,
  ``(``, ``)``, ``*``, ``"`` therefore never reach MATCH as syntax.
* CJK ideographs and kana are indexed **one character per token**, so a Chinese query can
  be a sub-string match even though ``unicode61`` would otherwise keep a whole CJK run as
  one token. Other scripts stay whole words (``cafe`` is not split into letters).
* An unquoted query is split on whitespace; runs of CJK characters become one *phrase*
  (adjacent characters in that order), and Latin/digit tokens become quoted *prefix*
  terms. All resulting parts are ANDed.
* A double-quoted segment is an explicit phrase and is kept verbatim (its own tokens in
  their own order, no prefix rewriting).
* CJK stopword characters end a run and are dropped; Latin stopword tokens are dropped as
  long as at least one real term remains. A query that has no term at all (empty, only
  separators/special characters, or only stopwords) is a structured ``VALIDATION_ERROR``
  instead of a silently empty result.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from photo_search.catalog.errors import CatalogError

#: Frozen generation of the tokenisation policy. A change to any rule above requires a new
#: number *and* a new :data:`FTS_GENERATION_ID`, because the stored ``search_documents``
#: rows are then stale and must be re-derived (architecture §4.3: rebuilding the FTS does
#: not re-run OCR or any model).
TOKENIZER_VERSION = 1
FTS_GENERATION_ID = "fts-v1"

#: The API code every refusal below uses; the frozen error envelope maps it to HTTP 422.
VALIDATION_ERROR = "VALIDATION_ERROR"

#: Bounds that keep one query from turning into an unbounded MATCH expression.
MAX_QUERY_CHARS = 256
MAX_QUERY_TERMS = 32

#: Grammar words only. Dropping them keeps a natural-language query from degenerating into
#: "every word must be present"; content words such as 照片/photo are never listed.
LATIN_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "in",
        "is",
        "it",
        "its",
        "my",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "was",
        "were",
        "with",
    }
)
CJK_STOPWORDS = frozenset("的得了是在和与及或之我这你那们个很都也就又")

#: CJK/kana ranges indexed one character per token.
_CJK_RANGES = (
    (0x2E80, 0x2EFF),  # CJK radicals
    (0x3040, 0x30FF),  # hiragana + katakana
    (0x3400, 0x4DBF),  # CJK unified ideographs extension A
    (0x4E00, 0x9FFF),  # CJK unified ideographs
    (0xF900, 0xFAFF),  # CJK compatibility ideographs
    (0x20000, 0x2FA1F),  # extensions B..F
)


def normalize_text(text: str) -> str:
    """NFKC + case folding, applied twice so the result is stable."""
    folded = unicodedata.normalize("NFKC", str(text)).casefold()
    return unicodedata.normalize("NFKC", folded)


def _is_token_char(char: str) -> bool:
    category = unicodedata.category(char)
    return category[0] in ("L", "N") or category == "Co"


def _is_cjk(char: str) -> bool:
    code = ord(char)
    return any(low <= code <= high for low, high in _CJK_RANGES)


def scan_tokens(text: str) -> list[str]:
    """The tokens ``unicode61`` will see in :func:`index_text` output, in order."""
    tokens: list[str] = []
    buffer: list[str] = []

    def flush() -> None:
        if buffer:
            tokens.append("".join(buffer))
            buffer.clear()

    for char in normalize_text(text):
        if not _is_token_char(char):
            flush()
            continue
        if _is_cjk(char):
            flush()
            tokens.append(char)
            continue
        if buffer and _is_cjk(buffer[-1]):
            flush()
        buffer.append(char)
    flush()
    return tokens


def index_text(text: str) -> str:
    """The normalised search text stored in ``search_documents`` for one source value."""
    return " ".join(scan_tokens(text))


@dataclass(frozen=True)
class Term:
    """One MATCH operand: a phrase (adjacent tokens) or a prefix term."""

    tokens: tuple[str, ...]
    phrase: bool

    def render(self) -> str:
        if self.phrase:
            return '"' + " ".join(self.tokens) + '"'
        return '"' + self.tokens[0] + '"*'


@dataclass(frozen=True)
class MatchQuery:
    """The frozen result of one query: a bound MATCH expression plus its provenance."""

    raw: str
    normalized: str
    expression: str
    terms: tuple[Term, ...]
    tokenizer_version: int = TOKENIZER_VERSION
    fts_generation_id: str = FTS_GENERATION_ID

    def term_texts(self) -> list[str]:
        return [term.render() for term in self.terms]


def _fail(message: str, **details: object) -> CatalogError:
    return CatalogError(VALIDATION_ERROR, message, details=dict(details))


def _pieces(raw: str) -> list[tuple[str, bool]]:
    """Split a raw query into ``(text, quoted)`` segments; an open quote is closed by EOF."""
    pieces: list[tuple[str, bool]] = []
    plain: list[str] = []
    quoted: list[str] = []
    in_quote = False
    for char in raw:
        if char == '"':
            if in_quote:
                pieces.append(("".join(quoted), True))
                quoted = []
            elif plain:
                pieces.append(("".join(plain), False))
                plain = []
            in_quote = not in_quote
            continue
        if in_quote:
            quoted.append(char)
        elif char.isspace():
            if plain:
                pieces.append(("".join(plain), False))
                plain = []
        else:
            plain.append(char)
    if in_quote:
        # An unterminated quote is not FTS syntax: the remainder is searched as plain text.
        pieces.append(("".join(quoted), False))
    elif plain:
        pieces.append(("".join(plain), False))
    return pieces


def _phrase_terms(piece: str) -> list[Term]:
    """Explicit phrase: keep the caller's token order, drop nothing, add no wildcard."""
    tokens = scan_tokens(piece)
    return [Term(tuple(tokens), True)] if tokens else []


def _plain_terms(piece: str) -> list[Term]:
    tokens = scan_tokens(piece)
    if not tokens:
        return []
    terms: list[Term] = []
    run: list[str] = []

    def flush() -> None:
        if run:
            terms.append(Term(tuple(run), True))
            run.clear()

    for token in tokens:
        if len(token) == 1 and _is_cjk(token):
            if token in CJK_STOPWORDS:
                flush()
                continue
            run.append(token)
            continue
        flush()
        if token in LATIN_STOPWORDS:
            continue
        terms.append(Term((token,), False))
    flush()
    return terms


def build_match(query: str) -> MatchQuery:
    """Turn one caller query into a bound MATCH expression, or refuse it with 422."""
    raw = "" if query is None else str(query)
    if len(raw) > MAX_QUERY_CHARS:
        raise _fail(
            f"the search text may be at most {MAX_QUERY_CHARS} characters",
            max_chars=MAX_QUERY_CHARS,
            length=len(raw),
        )
    terms: list[Term] = []
    for text, quoted in _pieces(raw):
        terms.extend(_phrase_terms(text) if quoted else _plain_terms(text))
    if not terms:
        raise _fail(
            "the search text has no searchable term",
            reason="NO_SEARCHABLE_TERM",
            tokenizer_version=TOKENIZER_VERSION,
        )
    if len(terms) > MAX_QUERY_TERMS:
        raise _fail(
            f"the search text has more than {MAX_QUERY_TERMS} terms",
            reason="TOO_MANY_TERMS",
            max_terms=MAX_QUERY_TERMS,
            terms=len(terms),
        )
    expression = " AND ".join(term.render() for term in terms)
    return MatchQuery(
        raw=raw,
        normalized=normalize_text(raw),
        expression=expression,
        terms=tuple(terms),
    )
