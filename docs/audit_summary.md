# Audit Summary

## Directory Structure
- `ait/`: Core application logic and orchestration.
- `static/`: Frontend assets (JS modules, CSS, HTML).
- `data/`: Application data and logs (default).
- `documentation/`: Project documentation.

## Dependency Identification
- **Runtime**: Python 3.x (standard library only, no external dependencies).
- **Frontend**: Vanilla JavaScript (ES modules), no external frameworks or bundlers.
- **Networking**: `http.server` and `urllib` for API communication.
- **Concurrency**: `threading` for background workers (watchdog, CI polling).
- **Data**: JSON-based storage for configuration and tasks.
