---
title: Documentation Index
project: hdgui
agent_project: hdgui
scope: documentation
purpose: Provides the central index and navigation for architecture, feature and user-guide documentation.
created: 2026-11-14
updated: 2026-11-14
update_reason: Introduce topic folders and a central documentation hub.
status: active
semantic_blocks:
  - id: documentation-folders
  - id: navigation
relations:
  - target: ../README.md
    type: parent
    reason: Repository overview and quick start.
---

<a id="documentation"></a>
# HDGUI Documentation

The documentation is grouped by purpose. Start from the relevant hub below; detailed pages link to related implementation and operation guidance.

## Documentation folders [documentation-folders]

| Folder | Status | Purpose | Semantic blocks |
|---|---|---|---|
| [Architecture](architecture/README.md) | active | Current system design and proposed catalog/data migrations | architecture-documents, navigation |
| [Features](features/README.md) | active | Delivered user-visible capabilities and limitations | feature-documents, navigation |
| [Guides](guides/README.md) | active | Operations and bilingual reflective-practice guides | guide-documents, navigation |

## Navigation [navigation]

- For the current implementation, start with [System Architecture](architecture/system-overview.md).
- For the future multilingual knowledge model, read the [Unified Knowledge Catalog proposal](architecture/knowledge-catalog-migration.md).
- For setup, upgrades and backups, see [Operations](guides/operations.md).
- For responsible reflective use, choose the [English](guides/practice.en.md) or [German](guides/practice.de.md) guide.
- For a short overview of delivered behavior, see the [Feature Catalog](features/feature-catalog.md).
