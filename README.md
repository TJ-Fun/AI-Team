# AI-Team

The web client is served from `static/` (`index.html`, `app.js`, and `style.css`). The server keeps application data and logs under `TEAM_DATA_DIR` (default: `data/`).

In **Settings → Logs**, **Store from** sets the minimum level written to the rotating log files and persists across restarts. **Show** filters the most recent matching entries in the log viewer. The **Documentation** button browses the local `documentation/` folder; **Files** browses the active project's configured local clone.

Git clone runs as a background job so a slow network fetch does not hold the browser request open. Progress and Git's final error output are written to `logs/app.log`; the project editor polls the job and displays the actual failure message. A failed clone is staged in a temporary sibling folder and removed, so retrying does not fail because of a partial destination. The Git status endpoint is also available as `GET /api/git/{project_id}/status`; it returns `Not cloned yet` before a local checkout exists.

For the supplied systemd service, configure project clone paths below `TEAM_DATA_DIR` (default `/var/lib/personal-ai-team/projects`). The service uses `ProtectHome=true`, so paths under `/home/ubuntu` are intentionally inaccessible even though they may be valid paths outside the service.