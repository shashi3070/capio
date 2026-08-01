# RFC-000: Capio Specification — Master Index

- **Status:** Accepted
- **Author:** Shashi Kundan
- **Created:** 2026-08-01
- **Part of:** Capio platform design program
- **Supersedes:** none

## Purpose

This document is the single entry point into the Capio architecture specification. It defines the
document conventions used by every RFC, maintains the canonical index of RFCs, and describes the
governance process by which the specification evolves.

The specification is deliberately large. Capio is designed as a platform, not a decorator library.
The documents that follow are the foundation on which contributors, plugin authors, and long-term
maintainers build. Read them in order the first time; use the index afterward.

## Status Legend

Every RFC carries one of these statuses:

| Status     | Meaning                                                        |
| ---------- | -------------------------------------------------------------- |
| Draft      | Under active writing; may change without notice.               |
| Accepted   | Reviewed and agreed; the canonical contract for that topic.    |
| Deprecated | Superseded by a later RFC; kept for historical reference.      |
| Implemented| Accepted and reflected in the reference implementation.        |

## Document Conventions

1. **Terminology.** Every defined term appears in its first use in **bold** and is catalogued in
   RFC-002 (Core Concepts & Glossary). All subsequent uses follow that definition exactly.
2. **Code.** Python snippets assume a package imported as `from capio import ...`. Illustrative
   code is annotated as *illustrative*; it is normative only where marked **normative**.
3. **Keywords.** The words "MUST", "MUST NOT", "SHALL", "SHALL NOT", "SHOULD", "SHOULD NOT",
   "RECOMMENDED", "MAY", and "OPTIONAL" carry RFC 2119 meaning in every document.
4. **Compatibility.** An RFC is binding from the moment it is marked **Accepted**. Changes require
   a new RFC or an amendment tracked in that RFC's change record.
5. **Forward references.** Cross-references use the `RFC-NNN` shorthand defined in the index below.

## Canonical Index

### Phase 1 — Foundation

| RFC | Title | Status |
| --- | ----- | ------ |
| RFC-030 | AI Capabilities — LLM, Agents, RAG, MCP | Draft |
| RFC-031 | Reference Implementation & Repository Structure | Draft |
| RFC-032 | Roadmap, Governance, Versioning, Ecosystem Strategy | Draft |
| RFC-033 | Migration Guide, Comparison Matrix, FAQ | Draft |

## How to Read This Specification

- **New to Capio:** read RFC-001, RFC-002, RFC-003, then RFC-004 and RFC-005. Everything else
  becomes background.
- **Plugin author:** read RFC-002, RFC-012, RFC-013, RFC-015, RFC-014, then the capability RFCs
  relevant to your plugin (RFC-016…RFC-022, RFC-030).
- **AI/LLM/agent developer:** read RFC-030 (AI suite), plus the platform core it composes with:
  RFC-005 (ordering), RFC-016 (cache), RFC-017 (retry), RFC-019 (GenAI observability),
  RFC-020 (AI audit), RFC-021 (tool/agent authorization), RFC-026 (AI security), and RFC-029
  (AI evaluation).
- **MCP integrator:** RFC-030 §7 plus RFC-006 (context propagation over MCP) and RFC-026 §9
  (MCP trust boundaries).
- **Maintainer / contributor:** read everything, in order, once. RFC-031 and RFC-032 define the
  repository and process you will work within.
- **Evaluator / architect:** RFC-001, RFC-004, RFC-005, RFC-026, RFC-032 are the decision-making
  documents.

## Change Record

| Version | Date | Change |
| ------- | ---- | ------ |
| 1.0     | 2026-08-01 | Initial publication of the specification index and document conventions. |
