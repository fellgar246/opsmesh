# ADR-012: Policy retrieval explains, it does not authorize

## Status

Accepted

## Context

Operators need a source they can open for an approval decision. A retrieved sentence that disagrees with the rules must not become the decision.

## Decision

Versioned policy documents live in `knowledge/policies`. Each file states its identifier, version, effective interval, and scope (`global` or a tenant id). A local index computes a SHA-256 checksum when it loads a file and serves search through a policy search port. `policy.search` returns citations: document id, version, checksum, location, and snippet. It does not return an approval or block flag.

Search keeps a document when its scope is global or the task tenant, the search date falls inside the effective interval, and the file still matches the checksum taken at load time. It returns at most five passages. A lower requested limit is honored. A changed file is omitted until the index is loaded again.

The policy agent attaches those citations to its result evidence and may name the document version and effective date in the explanation. Approval and block flags still come only from the deterministic policy service. A passage that denies a required approval produces the warning `policy_citation_contradicts_decision` and does not change the flags. When no passage is in scope, the explanation says the decision stands without a citation and the result includes the warning `policy_citation_missing`.

A managed knowledge base can implement the same search port later. The local index is the default.

## Consequences

- A manager-approval explanation can cite the indexed procurement or budget document, and the checksum matches that file.
- A contradictory passage cannot clear manager approval or a purchase block.
- A document for another tenant, or a document outside its effective dates, is not cited.
- A changed file is not served until the index is loaded again.
