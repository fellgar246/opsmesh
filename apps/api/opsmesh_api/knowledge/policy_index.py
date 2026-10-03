"""Local policy index. Search returns citations and never an approval flag."""

import hashlib
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol

from opsmesh_api.operations.policy import approval_for_total
from opsmesh_api.settings import REPO_ROOT

DEFAULT_MAX_PASSAGES = 5
WARNING_CITATION_MISSING = "policy_citation_missing"
WARNING_CITATION_CONTRADICTS = "policy_citation_contradicts_decision"

_TOKEN = re.compile(r"[a-z0-9]+")
_SCOPE = re.compile(r"^(?:global|[a-z][a-z0-9-]{0,63})$")
_REQUIRED_FIELDS = ("id", "version", "effective_start", "scope")
_OPTIONAL_FIELDS = frozenset({"effective_end"})
_DENIAL_PHRASES = (
    "no approval needed",
    "no approval required",
    "approval is not required",
    "approval is unnecessary",
    "does not require manager approval",
    "does not require finance approval",
    "does not require approval",
    "without approval",
)


class PolicyDocumentError(ValueError):
    """A policy file cannot be indexed."""


@dataclass(frozen=True)
class PolicySearch:
    """One retrieval request. The tenant on the task is authoritative."""

    tenant_id: str
    query: str
    as_of: date
    limit: int | None = None


@dataclass(frozen=True)
class PolicyCitation:
    """A passage from an indexed document. It does not authorize a purchase."""

    document_id: str
    version: str
    checksum: str
    location: str
    snippet: str
    effective_start: date
    effective_end: date | None

    def as_dict(self) -> dict[str, str | None]:
        end = None if self.effective_end is None else self.effective_end.isoformat()
        return {
            "document_id": self.document_id,
            "version": self.version,
            "checksum": self.checksum,
            "location": self.location,
            "snippet": self.snippet,
            "effective_start": self.effective_start.isoformat(),
            "effective_end": end,
        }


@dataclass(frozen=True)
class _Passage:
    location: str
    text: str


@dataclass(frozen=True)
class _Document:
    document_id: str
    version: str
    effective_start: date
    effective_end: date | None
    scope: str
    checksum: str
    path: Path
    passages: tuple[_Passage, ...]


class PolicySearchPort(Protocol):
    """Search port. A managed index can implement the same call later."""

    def search(self, request: PolicySearch) -> tuple[PolicyCitation, ...]:
        """Return at most the configured number of in-scope passages."""


def policies_directory() -> Path:
    """Return the product directory that holds policy documents."""

    return REPO_ROOT / "knowledge" / "policies"


def retrieval_query(
    *,
    extended_cost_cents: int | None = None,
    supplier_status: str | None = None,
    reason: str | None = None,
    requires_manager: bool | None = None,
    requires_finance: bool | None = None,
) -> str:
    """Choose search terms from the decision. The terms are not a decision."""

    manager = requires_manager
    finance = requires_finance
    if extended_cost_cents is not None and (manager is None or finance is None):
        derived_manager, derived_finance = approval_for_total(extended_cost_cents)
        if manager is None:
            manager = derived_manager
        if finance is None:
            finance = derived_finance
    parts: list[str] = []
    if reason == "supplier_not_approved" or supplier_status == "unapproved":
        parts.append("unapproved supplier")
    if reason == "insufficient_budget":
        parts.append("purchasing budget")
    if reason == "adequate_stock":
        parts.append("available stock")
    if reason == "missing_evidence":
        parts.append("missing evidence")
    if finance:
        parts.append("finance approval 25000")
    if manager:
        parts.append("manager approval 5000")
    return " ".join(parts)


def denies_required_approval(snippet: str) -> bool:
    """Return whether a passage denies an approval the rules may still require."""

    text = snippet.casefold()
    return any(phrase in text for phrase in _DENIAL_PHRASES)


class LocalPolicyIndex:
    """In-memory index of policy files. Checksums are fixed at load time."""

    def __init__(self, documents: tuple[_Document, ...], *, max_passages: int) -> None:
        if max_passages < 1:
            raise ValueError("The passage cap must be at least 1.")
        self._documents = documents
        self._max_passages = max_passages

    @classmethod
    def load(
        cls,
        directory: Path,
        *,
        max_passages: int = DEFAULT_MAX_PASSAGES,
    ) -> "LocalPolicyIndex":
        """Index the markdown files in one directory. Subdirectories are ignored."""

        documents: list[_Document] = []
        for path in sorted(directory.glob("*.md")):
            if path.name.casefold() == "readme.md" or not path.is_file():
                continue
            documents.append(_read_document(path))
        return cls(tuple(documents), max_passages=max_passages)

    def search(self, request: PolicySearch) -> tuple[PolicyCitation, ...]:
        """Filter by tenant and effective date, then cap the matching passages."""

        cap = self._max_passages
        if request.limit is not None:
            cap = min(cap, request.limit)
        if cap < 1:
            return ()
        ranked: list[tuple[int, str, str, PolicyCitation]] = []
        for document in self._documents:
            if not _visible(document, request.tenant_id):
                continue
            if not _in_effect(document, request.as_of):
                continue
            if _file_checksum(document.path) != document.checksum:
                continue
            for passage in document.passages:
                score = _score(request.query, passage.text)
                if score < 1:
                    continue
                ranked.append(
                    (
                        score,
                        document.document_id,
                        passage.location,
                        PolicyCitation(
                            document_id=document.document_id,
                            version=document.version,
                            checksum=document.checksum,
                            location=passage.location,
                            snippet=passage.text,
                            effective_start=document.effective_start,
                            effective_end=document.effective_end,
                        ),
                    )
                )
        ranked.sort(key=lambda item: (-item[0], item[1], item[2]))
        return tuple(item[3] for item in ranked[:cap])


_product_index: LocalPolicyIndex | None = None


def product_policy_index() -> LocalPolicyIndex:
    """Return the policy index loaded for this process.

    A file that changes after this load is not served until the process loads
    the index again.
    """

    global _product_index
    if _product_index is None:
        _product_index = LocalPolicyIndex.load(policies_directory())
    return _product_index


def _read_document(path: Path) -> _Document:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PolicyDocumentError(f"{path.name} is not UTF-8.") from exc
    header, body = _split_header(text, path.name)
    effective_end = header.get("effective_end") or None
    return _Document(
        document_id=header["id"],
        version=header["version"],
        effective_start=_parse_date(header["effective_start"], path.name, "effective_start"),
        effective_end=(
            None
            if effective_end is None
            else _parse_date(effective_end, path.name, "effective_end")
        ),
        scope=header["scope"],
        checksum=hashlib.sha256(raw).hexdigest(),
        path=path,
        passages=_passages(body, path.name),
    )


def _split_header(text: str, name: str) -> tuple[dict[str, str], str]:
    lines = text.splitlines()
    header: dict[str, str] = {}
    body_at = len(lines)
    for index, line in enumerate(lines):
        if not line.strip():
            body_at = index + 1
            break
        if ":" not in line:
            raise PolicyDocumentError(f"{name} has a header line without a field name.")
        key, value = line.split(":", 1)
        field = key.strip()
        if field in header:
            raise PolicyDocumentError(f"{name} repeats {field}.")
        header[field] = value.strip()
    unknown = sorted(set(header) - set(_REQUIRED_FIELDS) - _OPTIONAL_FIELDS)
    if unknown:
        names = ", ".join(unknown)
        raise PolicyDocumentError(f"{name} has unknown header fields: {names}.")
    missing = [field for field in _REQUIRED_FIELDS if not header.get(field)]
    if missing:
        names = ", ".join(missing)
        raise PolicyDocumentError(f"{name} is missing {names}.")
    if not _SCOPE.fullmatch(header["scope"]):
        raise PolicyDocumentError(f"{name} has a scope that is not global or a tenant id.")
    return header, "\n".join(lines[body_at:])


def _parse_date(value: str, name: str, field: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise PolicyDocumentError(f"{name} has an invalid {field}.") from exc


def _passages(body: str, name: str) -> tuple[_Passage, ...]:
    chunks = re.split(r"(?m)^## ", body)
    passages: list[_Passage] = []
    preamble = chunks[0].strip()
    if preamble:
        passages.append(_Passage("body", preamble))
    for chunk in chunks[1:]:
        title, _separator, remainder = chunk.partition("\n")
        location = title.strip()
        text = remainder.strip()
        if not location or not text:
            raise PolicyDocumentError(f"{name} has a passage without a location or text.")
        passages.append(_Passage(location, text))
    if not passages:
        raise PolicyDocumentError(f"{name} has no passages.")
    return tuple(passages)


def _visible(document: _Document, tenant_id: str) -> bool:
    return document.scope == "global" or document.scope == tenant_id


def _in_effect(document: _Document, as_of: date) -> bool:
    if as_of < document.effective_start:
        return False
    return document.effective_end is None or as_of <= document.effective_end


def _file_checksum(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _score(query: str, snippet: str) -> int:
    wanted = set(_TOKEN.findall(query.casefold()))
    if not wanted:
        return 0
    present = set(_TOKEN.findall(snippet.casefold()))
    return sum(1 for token in wanted if token in present)
