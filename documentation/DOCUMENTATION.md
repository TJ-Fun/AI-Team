# Technical Overview

## Runtime and hosting
The application runs as a Python-based HTTP server using `http.server.ThreadingHTTPServer`. It is designed to be deployed as a systemd service, utilizing a dedicated workspace for project clones and maintaining persistent application data in `TEAM_DATA_DIR` (default: `data/`).

## Languages and frameworks
- **Backend**: Python 3.x, utilizing standard library modules for HTTP handling, threading, and JSON processing.
- **Frontend**: Vanilla JavaScript (ES modules) served from `static/`, with no external build tools or bundlers.

## Architecture and integrations
- **Orchestration Pattern**: The system uses a centralized `ait` package that flattens domain modules into a shared namespace, allowing cross-module function calls while maintaining logical separation. Background tasks (Git operations, watchdog, CI polling) are managed via daemon threads.
- **Agent Communication**: Orchestration is driven by task-based delegation. The PM/PO role manages planning, while other agents (QA, etc.) operate on tasks within a project-specific context. Communication with AI providers is abstracted through a provider registry, supporting both native Gemini integration and OpenAI-compatible endpoints.
- **Task Delegation**: Workflows are serialized per GitHub repository. The system uses a guarded sync mechanism for local checkouts, ensuring that divergent or dirty states are handled via notifications rather than automated destructive actions.
- **External Integrations**: 
    - **AI Providers**: Configurable via `Connections` (OpenAI-compatible or Gemini).
    - **Version Control**: Git integration for project management, with background job polling for status and progress.
    - **Systemd**: Integration for process management, sandboxing, and persistent storage access.

## Data and storage
Application state, logs, and project data are stored in the local filesystem. Configuration is managed via JSON-based app settings, and task boards are persisted as structured data within the project workspace.

## How to build, run, and check
- **Run**: Execute `python server.py`.
- **Check**: Use `python -m compileall -q ait server.py` for syntax validation and `node --check` for frontend modules.

## Known limitations
Documentation is currently being established; the codebase is pre-existing and relies on a flat-namespace import pattern.