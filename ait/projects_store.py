"""Project list persistence, validation, and legacy context migration."""

from __future__ import annotations

from ait.common import *


# --- Projects ---
def migrate_legacy_project_context(projects: list[dict[str, Any]]) -> None:
    with STATE_LOCK:
        legacy_root = DATA / "project"
        valid_projects = [project for project in projects if isinstance(project, dict)]
        if not legacy_root.is_dir() or not valid_projects:
            return
        target_project = next((p for p in valid_projects if p.get("id") == "default"), valid_projects[0])
        project_id = str(target_project.get("id", ""))
        if not re.fullmatch(r"[a-z0-9-]+", project_id):
            return

        context_root = DATA / "projects" / project_id / "context"
        for slug in PROJECT_CONTEXT_FILES:
            legacy_file = legacy_root / f"{slug}.md"
            target_file = context_root / f"{slug}.md"
            if legacy_file.is_file() and not target_file.exists():
                target_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(legacy_file), str(target_file))

            legacy_history = legacy_root / "history" / slug
            target_history = context_root / "history" / slug
            if legacy_history.is_dir():
                for snapshot in legacy_history.glob("*.md"):
                    target_snapshot = target_history / snapshot.name
                    if not target_snapshot.exists():
                        target_history.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(snapshot), str(target_snapshot))


def read_projects() -> list[dict[str, Any]]:
    ensure_data()
    projects_path = DATA / "projects.json"
    if projects_path.is_file():
        try:
            items = json.loads(projects_path.read_text(encoding="utf-8"))
        except Exception:
            items = None
        if isinstance(items, list) and items:
            migrate_legacy_project_context(items)
            return items
    default_project = [
        {"id": "default", "name": "Main project", "repository": "", "context": "", "modules": []}
    ]
    atomic_json(projects_path, default_project)
    migrate_legacy_project_context(default_project)
    return default_project


def paused_project_ids() -> set[str]:
    return {str(p.get("id")) for p in read_projects() if p.get("active") is False}


def save_projects(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list) or not items:
        raise ValueError("Keep at least one project.")
    existing_map = {p["id"]: p for p in read_projects() if isinstance(p, dict) and "id" in p}
    seen: set[str] = set()
    cleaned: list[dict[str, Any]] = []

    for raw in items:
        if not isinstance(raw, dict):
            raise ValueError("Each project must be an object.")
        pid = re.sub(r"[^a-z0-9-]+", "-", str(raw.get("id", "")).lower()).strip("-")[:40]
        name = str(raw.get("name", "")).strip()[:100]
        repository = str(raw.get("repository", "")).strip()
        local_path = str(raw.get("local_path", "")).strip()
        modules = raw.get("modules", [])
        if not pid or pid in seen or not name or not isinstance(modules, list):
            raise ValueError("Project names and unique IDs are required; modules must be a list.")
        if repository and not re.fullmatch(
            r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?", repository
        ):
            raise ValueError(f"Repository for {name} must be a GitHub https URL.")
        if local_path:
            candidate = Path(local_path).expanduser()
            if not candidate.is_absolute() or ".." in candidate.parts:
                raise ValueError(f"Local path for {name} must be an absolute path without '..'.")
        clean_modules: list[dict[str, str]] = []
        mod_ids: set[str] = set()
        for mod in modules:
            if not isinstance(mod, dict):
                raise ValueError("Each sub-project must be an object.")
            mid = re.sub(r"[^a-z0-9-]+", "-", str(mod.get("id", "")).lower()).strip("-")[:40]
            mname = str(mod.get("name", "")).strip()[:100]
            mpath = str(mod.get("path", "")).strip().replace("\\", "/").strip("/")
            if not mid or mid in mod_ids or not mname or ".." in mpath.split("/"):
                raise ValueError("Each sub-project needs a unique ID, name, and safe repository folder path.")
            mod_ids.add(mid)
            clean_modules.append(
                {
                    "id": mid,
                    "name": mname,
                    "path": mpath,
                    "context": str(mod.get("context", ""))[:10000],
                }
            )
        raw_services = raw.get("services")
        if raw_services is None:
            services = clean_project_services((existing_map.get(pid, {}) or {}).get("services") or {})
        else:
            services = clean_project_services(raw_services)
        seen.add(pid)
        context = str(raw.get("context", ""))[:20000]
        old_ctx = str(existing_map.get(pid, {}).get("context", ""))
        if old_ctx and old_ctx != context:
            snap = DATA / "projects" / pid / "history" / f"{make_version_id()}.md"
            snap.parent.mkdir(parents=True, exist_ok=True)
            snap.write_text(old_ctx, encoding="utf-8")
        old_proj = existing_map.get(pid, {})
        try:
            version = max(1, int(raw.get("version") or old_proj.get("version") or 1))
        except (TypeError, ValueError):
            version = 1
        raw_phases = raw.get("completed_phases") if isinstance(raw.get("completed_phases"), list) else old_proj.get("completed_phases")
        completed_phases = sorted({int(p) for p in (raw_phases or []) if str(p).isdigit()})
        raw_milestones = raw.get("milestones") if isinstance(raw.get("milestones"), list) else old_proj.get("milestones")
        milestones = [m for m in (raw_milestones or []) if isinstance(m, dict)][:100]
        try:
            active_phase = int(raw.get("active_phase") or old_proj.get("active_phase") or 0) or None
        except (TypeError, ValueError):
            active_phase = None
        if active_phase is not None:
            active_phase = min(max(active_phase, 1), 9)
        active = bool(raw["active"]) if "active" in raw else bool(old_proj.get("active", True))
        cleaned.append(
            {
                "id": pid,
                "name": name,
                "repository": repository,
                "local_path": local_path,
                "context": context,
                "modules": clean_modules,
                "services": services,
                "version": version,
                "completed_phases": completed_phases,
                "milestones": milestones,
                "active_phase": active_phase,
                "active": active,
            }
        )

    task_pids = {str(t.get("project_id") or "default") for t in read_tasks()}
    for pid in task_pids:
        if pid not in seen:
            raise ValueError(
                "Projects with existing tasks cannot be removed. Keep them and edit their details instead."
            )
    atomic_json(DATA / "projects.json", cleaned)
    return cleaned


def project_background_history(project_id: str) -> list[dict[str, str]]:
    if not any(p.get("id") == project_id for p in read_projects()):
        return []
    folder = DATA / "projects" / project_id / "history"
    if not folder.is_dir():
        return []
    rows: list[dict[str, str]] = []
    for f in sorted(folder.glob("*.md"), reverse=True):
        try:
            txt = f.read_text(encoding="utf-8")
            rows.append(
                {
                    "id": f.stem,
                    "created_at": parse_version_timestamp(f.stem),
                    "preview": re.sub(r"\s+", " ", txt).strip()[:140],
                }
            )
        except Exception:
            continue
    return rows
