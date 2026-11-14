---
title: Unified Knowledge Catalog
project: hdgui
agent_project: hdgui
scope: architecture
purpose: Defines the proposed shared knowledge model and a safe migration from the current separate catalogs.
created: 2026-11-14
updated: 2026-11-14
update_reason: Extract the unified multilingual catalog design from the system architecture overview.
status: draft
semantic_blocks:
  - id: knowledge-model
  - id: multilingual-content
  - id: expand-migrate-contract-rollout
relations:
  - target: README.md
    type: parent
    reason: Architecture documentation index.
  - target: system-overview.md
    type: reference
    reason: Describes the delivered schema and current catalog boundaries.
  - target: ../guides/operations.md
    type: reference
    reason: Current migrations, imports and operational behavior.
  - target: ../README.md
    type: reference
    reason: Top-level documentation navigation.
---

# Unified Knowledge Catalog

[Architecture index](README.md) · [System architecture](system-overview.md) · [Operations](../guides/operations.md)

The target is one knowledge domain with stable identities, structured facts, relationships and localized editorial text—not one table that mixes all of those concerns. It replaces parallel text stores while keeping domain constraints explicit. This is a proposed migration, not behavior delivered by revision `0007`.

<a id="knowledge-model"></a>
## Target data model [knowledge-model]

- **`knowledge_entities`** is the shared identity registry. Stable IDs such as `gate.1`, `channel.1-8`, `circuit.integration`, `body.Sun`, and the existing HD IDs identify concepts independently of their display names and language. Domain/category and immutable or calculation-derived structure belong to the concept, not a translation.
- **`knowledge_entity_texts`** stores one localized content record per `(entity_id, locale, text_kind)`: display name and description, with per-translation origin, author, model, date and review state. `text_kind` keeps distinct lenses—such as Human Design, astrology symbolism and astronomy facts—separate even when they refer to the same body. Locale tags use normalized BCP 47 values (`de`, `en`, and, if needed, regional tags such as `de-CH`). A language or text variant is not a new entity.
- Existing relational topology remains normalized and authoritative: gates have centers; channels have two gates and one circuit; circuits group channels. Existing foreign keys and checks continue enforcing those rules. The registry gives those records shared identities and localized text; a generic relation table is added only for cross-domain links that cannot be represented by the domain relations.
- Astrology symbolism, astronomical facts and Human Design concepts remain distinct domains even when they refer to the same body or point. Their records can link to the same `body.*` identity, but their localized text has an explicit text kind/source so a symbolic interpretation cannot overwrite a factual description.
- Natal snapshots keep calculated raw values and catalog IDs. They reference stable entities; translation edits and locale selection never change a calculation or its cache key.

## Multilingual content and fallback [multilingual-content]

The current German UI is the initial default. Catalog content starts with German (`de`); English (`en`) can be added per entry without changing IDs or schema. UI labels and messages are a separate localization layer (gettext message catalogs), not rows in the knowledge database. For this single-user application, an explicit saved locale preference takes precedence; otherwise negotiate `Accept-Language`, then use `de`.

Resolve catalog text by exact selected locale first, then progressively less-specific locale tags (for example `de-CH` to `de`), then the configured default locale. Mark fallback text with its actual language; never silently present another language as if it were the requested translation. If neither translation exists, show the stable concept ID and an explicit missing-translation state. Draft translations remain distinguishable from reviewed ones.

<a id="expand-migrate-contract-rollout"></a>
## Expand–migrate–contract rollout [expand-migrate-contract-rollout]

Use ordinary Alembic revisions against the existing database; users should not export and manually re-enter catalog records. Keep the rollout additive until all current pages and importers use the shared text source:

1. **Inventory and stable mapping:** define deterministic IDs and map all 64 gates, 36 channels, seven circuits, 13 celestial bodies, existing HD vocabulary, 12 Human Design profiles and 384 gate-line slots. Identify the source and `text_kind` of each existing name/description. Preserve every current value exactly; mark provenance as legacy/unknown when it cannot be verified rather than guessing an author or language.
2. **Expand schema:** add the shared entity registry, localized text table, locale preference and only the cross-domain links needed for navigation. Seed entities from canonical topology and existing HD structures. Keep the old text columns/tables intact in this revision so the upgrade is recoverable and old code still starts.
3. **Copy and verify in the migration:** copy gate/channel/circuit text into `de`; extract the existing celestial-body page into a checked-in fixture and import it as `de` with its existing AI-authorship notice; copy HD entity text and its known per-entry provenance. An explicit mapping/report must account for every source record, duplicates and null descriptions. Leave English translations absent or draft rather than machine-translating them implicitly.
4. **Switch application reads and writes:** route all catalogs and explicit import/export commands through one repository/service over entity identities and locale rows. Update natal links and profile pages to consume localized names. During a short compatibility window, if an old writer cannot yet be removed, dual-write inside one transaction and test parity; do not allow two unsynchronized sources of truth.
5. **Unify presentation and localization:** make `/knowledge` the landing page for gates, channels/circuits, celestial bodies and HD entities; keep their existing URLs as compatible filtered views. Add a language selector and gettext-based UI messages independently of catalog content. Show content locale and fallback/review state; provide navigation via stable entity IDs.
6. **Contract only after verification:** compare migrated values and record counts, confirm all references resolve, exercise imports/pages in both locales, and back up the database. In a later Alembic revision, remove obsolete localized name/description columns and retire the old HD text store. Avoid combining a destructive cleanup with the initial data-copy revision; retain a documented restore path from the pre-contract backup.

Migration tests should prove exact preservation of existing text and nulls, unique `(entity_id, locale, text_kind)` records, all expected structural counts and references, idempotent fixture imports, human-text protection, locale negotiation, explicit fallback labeling, and no catalog or chart changes when switching locale. The current schema remains in place until this plan is implemented and verified.
