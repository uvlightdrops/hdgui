---
title: Feature Documentation
project: hdgui
agent_project: hdgui
scope: features
purpose: Indexes documentation for delivered user-facing HDGUI features.
created: 2026-11-14
updated: 2026-11-14 19
update_reason: Mark the topic-based knowledge UI as delivered.
status: active
semantic_blocks:
  - id: feature-documents
  - id: navigation
relations:
  - target: ../README.md
    type: parent
    reason: Project documentation index.
  - target: ../architecture/README.md
    type: reference
    reason: Architecture behind the documented features.
---

# Features

This folder describes user-visible behavior and distinguishes delivered features from proposals.

## Feature documents [feature-documents]

| File | Status | Purpose | Semantic blocks |
|---|---|---|---|
| [Feature Catalog](feature-catalog.md) | active | Inventory of delivered features and limitations | transit-exploration, birth-profiles, timeline-analysis, center-timeline, catalogs-learning, settings-data, not-delivered |
| [Knowledge UI](knowledge-ui.md) | active | Four-topic navigation, category mapping, route state and acceptance criteria | Information architecture, navigation, implementation and acceptance |

## Navigation [navigation]

- Start with the [Feature Catalog](feature-catalog.md).
- See [System Architecture](../architecture/system-overview.md) for implementation details.
- See [Guides](../guides/README.md) for operating the application and responsible practice.
