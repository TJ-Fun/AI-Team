# Technical Report: AI Team Repository Audit

## 1. Repository Structure
- `ait/`: Core Python backend package containing orchestration logic, API handlers, and domain modules.
- `static/`: Frontend assets (HTML, CSS, JS). The `static/js/` directory contains modularized ES6 code.
- `data/`: Default directory for application data, logs, and project files.
- `documentation/`: Project documentation files.

## 2. Primary Language and Dependencies
- **Primary Language**: Python 3 (Backend), JavaScript (Frontend).
- **Core Dependencies**:
  - Python standard library (heavy reliance on `urllib`, `threading`, `json`, `http.server`).
  - No external Python package dependencies (e.g., `pip` packages) are explicitly required by the current codebase; it relies on the standard library.
  - Frontend uses vanilla ES modules without external build tools or bundlers.

## 3. Orchestration Entry Point
- **Backend**: `ait/main.py` serves as the entry point for the server and background workers (`start_background_workers`). The `ait/__init__.py` file handles the flat namespace injection, allowing domain modules (like `ait/chat.py`, `ait/git_ops.py`) to interact seamlessly.
- **Frontend**: `static/app.js` is the primary entry point for the web client, which initializes feature modules and manages the application state.
- **Orchestration Logic**: The core orchestration logic is distributed across `ait/chat.py` (provider interaction), `ait/git_ops.py` (repository management), and `ait/providers.py` (routing and task delegation).