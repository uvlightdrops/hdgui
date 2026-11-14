---
title: Guides Documentation
project: hdgui
agent_project: hdgui
scope: guides
purpose: Indexes operational instructions and bilingual reflective-practice guides.
created: 2026-11-14
updated: 2026-11-14 19
update_reason: Mark the German knowledge navigation guide as current.
status: active
semantic_blocks:
  - id: guide-documents
  - id: navigation
relations:
  - target: ../README.md
    type: parent
    reason: Project documentation index.
  - target: ../architecture/README.md
    type: reference
    reason: Architecture and proposed catalog design.
  - target: ../features/README.md
    type: reference
    reason: User-facing feature documentation.
---

# Guides

This folder contains practical operations instructions and bilingual guides for responsible use and reflection.

## Guide documents [guide-documents]

| File | Status | Purpose | Semantic blocks |
|---|---|---|---|
| [Operations](operations.md) | active | Installation, configuration, migration, catalog import, backup and troubleshooting | api-key-setup, installation-updates, configuration, startup-docker-lifecycle, catalog-imports, storage-time-settings, backup-restoration, security-licensing, troubleshooting |
| [Practice — English](practice.en.md) | active | Reflective workflows, uncertainty, consent and future possibilities | purpose-boundaries, current-application, reflective-workflows, time-uncertainty, consent-privacy, future-possibilities |
| [Praxis — Deutsch](practice.de.md) | active | Deutsche Fassung des Leitfadens zu Reflexion, Unsicherheit und verantwortungsvoller Nutzung | zweck-grenzen, anwendung-heute, arbeitsweisen, zeit-unsicherheit, einwilligung-privatsphaere, weiterentwicklungen |
| [Wissen erkunden](knowledge-ui.de.md) | active | Themen-Tabs, Kategorien und Einträge aus Anwendersicht | Themenwahl, Navigation, Textkennzeichnung und Grenzen |

## Navigation [navigation]

- For installation, database and maintenance procedures, start with [Operations](operations.md).
- For reflective use, read the [English](practice.en.md) or [German](practice.de.md) practice guide.
- For current implementation details, see [System Architecture](../architecture/system-overview.md).
