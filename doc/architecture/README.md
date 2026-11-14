---
title: Architecture Documentation
project: hdgui
agent_project: hdgui
scope: architecture
purpose: Indexes the current system architecture and proposed catalog/data-model design.
created: 2026-11-14
updated: 2026-11-14
update_reason: Create a semantic documentation hub for architecture references.
status: active
semantic_blocks:
  - id: architecture-documents
  - id: navigation
relations:
  - target: ../README.md
    type: parent
    reason: Project documentation index.
  - target: ../guides/README.md
    type: reference
    reason: Operational and practice guides related to the architecture.
---

# Architecture

This folder separates the description of the delivered system from proposals for future data architecture and migrations.

## Architecture documents [architecture-documents]

| File | Status | Purpose | Semantic blocks |
|---|---|---|---|
| [System Architecture](system-overview.md) | active | Runtime, domain, persistence, API flows, caches and current limitations | runtime-boundaries, module-responsibilities, domain-persistence, api-presentation-flows, cache-concurrency, accuracy-limitations, proposed-developments |
| [Unified Knowledge Catalog](knowledge-catalog-migration.md) | active | Shared multilingual entity/text model, implemented additive migration and pending contract phase | knowledge-model, multilingual-content, expand-migrate-contract-rollout |

## Navigation [navigation]

- Start with [System Architecture](system-overview.md) for what is implemented today.
- Read [Unified Knowledge Catalog](knowledge-catalog-migration.md) for revision `0008` behavior and the remaining contract work.
- See [Operations](../guides/operations.md) for commands, backups and existing database migrations.
- See [Feature Catalog](../features/feature-catalog.md) for user-facing capabilities.
