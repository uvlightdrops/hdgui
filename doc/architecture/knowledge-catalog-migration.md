---
title: Unified Knowledge Catalog
project: hdgui
agent_project: hdgui
scope: architecture
purpose: Defines the proposed shared knowledge model and a safe migration from the current separate catalogs.
created: 2026-11-14
updated: 2026-11-14
update_reason: Record the implemented additive catalog migration, locale handling and remaining contract phase.
status: active
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

The target is one knowledge domain with stable identities, structured facts, relationships and localized editorial text—not one table that mixes all of those concerns. Revision `0008` implements the additive identity/text registry, migrates existing catalog wording, and switches catalog presentation to localized text records. Legacy tables and text columns remain as a compatibility layer; their removal is intentionally deferred.

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

Use ordinary Alembic revisions against the existing database; users should not export and manually re-enter catalog records. Revision `0008` completes the additive expand-and-copy stage:

1. **Inventory and stable mapping — implemented:** deterministic IDs cover 64 gates, 36 channels, seven circuits and all seeded HD structures, including 13 shared celestial-body identities and 384 gate-line slots. Legacy records without reliable provenance are marked `legacy`/`unknown`; no author or locale is inferred from individual values.
2. **Expand schema — implemented:** revision `0008` adds `knowledge_entities`, `knowledge_entity_texts` and nullable `ui_settings.catalog_locale`. Existing tables and text columns remain intact.
3. **Copy and verify — implemented:** the migration copies gate/channel/circuit and HD text into `de`, preserving nulls and known HD provenance. The existing celestial-symbolism content is held in a checked-in fixture and imported as `de` with its existing AI attribution. English content is not generated or implied.
4. **Switch readers and writers — compatibility phase implemented:** catalog pages, profile properties and transit gate descriptions resolve text from the shared table. Existing gate and HD importers write the localized store and legacy columns in the same transaction. Stable IDs remain unchanged in natal snapshots; the legacy columns are retained until a later contract revision.
5. **Locale presentation — catalog content implemented:** a saved `de`/`en` preference overrides `Accept-Language`; otherwise the request locale is negotiated with German as the default. Resolution uses exact locale, progressively less-specific tags and German fallback, and displays the actual content locale. The surrounding UI remains German; UI message translation with gettext is separate future work.
6. **Contract — not performed:** before removing legacy columns/tables, compare migrated values and counts, verify references and import/render behavior, and back up the database. Keep destructive cleanup in a later revision with a documented restore path.

Migration tests prove preservation of existing text and nulls, structural counts, seeded celestial provenance, fallback behavior, saved locale precedence, human-text protection and reversible additive schema changes. The contract phase remains pending; locale changes affect presentation only and do not rewrite natal snapshots or calculation keys.
