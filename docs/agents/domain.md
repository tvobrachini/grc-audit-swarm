# Domain Docs

This repo uses a **single-context** layout.

## Layout

| Artifact | Path |
|----------|------|
| Domain context | `CONTEXT.md` (repo root) |
| Architecture decision records | `docs/adr/` |

## Consumer rules for skills

- Read `CONTEXT.md` before reasoning about domain language, bounded contexts, or terminology.
- Read all files under `docs/adr/` (sorted by filename) to understand past architectural decisions before proposing new ones.
- If `CONTEXT.md` does not exist yet, proceed without it and note the gap — do not fabricate domain context.
- If `docs/adr/` does not exist yet, proceed without ADRs — do not fabricate decisions.

## Creating new ADRs

Name files `docs/adr/NNNN-short-title.md` (zero-padded 4-digit index). Use the [MADR](https://adr.github.io/madr/) template unless the repo already has an established format.
