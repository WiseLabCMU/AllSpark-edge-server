# Contributing to AllSpark Edge Server

This document covers **development rules and conventions** specific to `AllSpark Edge Server`. These rules are mandatory for all contributors, including automated/agentic coding tools.

## Development Rules

### 1. Unified Configuration (YAML)

All operational settings must be stored in `config.yaml`.
- **No JSON:** The server has been refactored away from legacy JSON configurations. Do not write or rely on `.json` settings files.
- **Namespaces:** Configurations must be split into two explicit namespaces: `mobile_client` (for device telemetry and synchronization endpoints) and `control_plane` (web dashboard and dashboard dependencies).
- **Auto-generation:** The server must automatically handle fallback implementations and auto-generate any missing safe default configuration schemas upon launch.

### 2. Architecture & UI

- **Python First:** The server backend and GUI are python based. Do not use legacy Node.js workflows.
- **NiceGUI Control Plane:** The Edge server control plane is built using [NiceGUI](https://nicegui.io/). Routing is natively managed by decorators (e.g. `@ui.page('/route')`). When creating pages, add a call inside the main process to initialize them dynamically.

### 3. File-System Signals

For anomaly event generation and signaling, do not deploy heavyweight MQTT brokers on the edge infrastructure unless absolutely required by other external systems. Utilize local native directory-watching mechanisms for tracking events and system logs.

### 4. Dependencies — Pin All Versions

**All dependencies must use exact, pegged versions** (no `^`, `~`, or `*` ranges). This prevents version drift across environments and ensures reproducible builds for security.

## Code Style & Versioning

- Enforce proper Python styling guides (`flake8` / `black` when applicable).
- Keep requirements updated using frozen `requirements.txt`.

The `allspark-agents` repository (including this Edge Server subproject) uses [Release Please](https://github.com/googleapis/release-please) to automate CHANGELOG generation and semantic versioning. Your PR titles *must* follow Conventional Commit standards (e.g., `feat:`, `fix:`, `chore:`).
