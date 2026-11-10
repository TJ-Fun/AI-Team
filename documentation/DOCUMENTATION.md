# Technical Overview

## Runtime and hosting
- **Runtime:** Python 3.x (standard library only, no external dependencies).
- **Hosting:** Designed to run as a local service (e.g., via `systemd`) on Linux. The application uses a `ThreadingHTTPServer` for the web interface and background worker threads for task orchestration, Git operations, and CI polling.

## Languages and frameworks
- **Backend:** Python. Uses a modular architecture where domain modules (e.g., `chat.py`, `git_ops.py`, `providers.py`) share a flat namespace via `ait/__init__.py` to maintain compatibility with the original single-file structure.
- **Frontend:** Vanilla JavaScript (ES modules). The client is served from `static/` and is currently undergoing a modularization refactor (see `ToDo/frontend-app-js-split.md`).

## Architecture and integrations
- **Agent Orchestration:** The system manages autonomous agents through a task-based board. Agents are assigned roles and utilize configured AI providers (OpenAI-compatible or Gemini) to process tasks.
- **Communication:** Agents communicate via a centralized task board and project-specific context. The `chat.py` module handles direct user-to-agent interaction.
- **Integrations:** 
    - **Git:** Background jobs handle repository cloning and synchronization.
    - **AI Providers:** Supports multiple providers with an 'Auto' fallback mechanism based on audit scores and connection order.

## Data and storage
- **Persistence:** Application data, logs, and project files are stored in a configurable directory (default: `data/`).
- **Logs:** Rotating log files are managed by the server and accessible via the web UI.

## How to build, run, and check
- **Run:** Execute `python server.py` to start the server.
- **Check:** The system includes a `main.py` entry point that initializes background workers and the HTTP server. Frontend code can be verified using `node --check` on individual modules.

## Known limitations
- Documentation is an ongoing effort; codebase is pre-existing and currently undergoing structural refactoring.