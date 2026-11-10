# Architecture Summary

## Overview
AI-Team is a modular AI agent orchestration framework. It uses a Python-based server for backend logic and a modular JavaScript frontend.

## Backend Structure (`ait/`)
- `ait/__init__.py`: Implements a flat namespace pattern, injecting public names from domain modules into each other to maintain legacy compatibility while supporting modularity.
- `ait/common.py`: Core utilities, state management, and shared primitives.
- `ait/server.py`: HTTP server entry point and request handling.
- `ait/main.py`: Background worker orchestration (watchdog, CI polling).
- `ait/chat.py`: PM/PO chat integration and provider communication.
- `ait/git_ops.py`: Git repository management and background job handling.
- `ait/provider_registry.py` & `ait/providers.py`: AI provider configuration and routing logic.

## Frontend Structure (`static/`)
- `static/app.js`: Entry point for the ES module-based frontend.
- `static/js/core.js`: Shared state, API helpers, and UI primitives.
- `static/js/`: Feature-specific modules (e.g., `header.js`, `providers.js`, `project-settings.js`) extracted during the ongoing refactor.

## Dependencies
- **Runtime**: Python 3.x (standard library focus, no heavy frameworks).
- **Frontend**: Vanilla JavaScript (ES modules), no external build tools or bundlers.
- **Storage**: Local filesystem (JSON-based configuration and data files).
- **Integrations**: OpenAI-compatible API endpoints and Google Gemini API.