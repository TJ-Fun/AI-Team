"""Routing, projects, tasks, context documents, and chat history."""

from __future__ import annotations

from ait.common import *


# --- Routing, Projects, Tasks, Context ---
def _clean_route_model(value: Any) -> str:
    model = str(value or "").strip()
    if not model or model.lower() == "auto":
        return "auto"
    return model[:160]


def _routing_entries(raw: Any, active: set[str]) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    items = raw if isinstance(raw, list) else []
    for item in items:
        if isinstance(item, dict):
            provider = str(item.get("provider", "")).strip()
            model = _clean_route_model(item.get("model"))
        else:
            provider = str(item).strip()
            model = "auto"
        if provider == "Auto":
            model = "auto"
        elif provider not in active:
            continue
        entries.append({"provider": provider, "model": model})
        if len(entries) >= MAX_ROUTE_ENTRIES:
            break
    return entries


def get_routing() -> dict[str, list[dict[str, str]]]:
    ensure_data()
    routing_path = DATA / "settings" / "routing.json"
    saved: dict[str, Any] = {}
    if routing_path.is_file():
        try:
            saved = json.loads(routing_path.read_text(encoding="utf-8"))
        except Exception:
            saved = {}
    active = set(provider_names())
    result: dict[str, list[dict[str, str]]] = {}
    for role in WORK_ROLES:
        raw = saved.get(role) if isinstance(saved, dict) else None
        result[role] = _routing_entries(raw, active)
    return result


def save_routing(value: Any) -> dict[str, list[dict[str, str]]]:
    if not isinstance(value, dict):
        raise ValueError("Routing settings must be an object.")
    active = set(provider_names())
    cleaned: dict[str, list[dict[str, str]]] = {}
    for role in WORK_ROLES:
        order = value.get(role, [])
        if not isinstance(order, list):
            raise ValueError(f"Provider preference for {role} must be a list.")
        cleaned[role] = _routing_entries(order, active)
    atomic_json(DATA / "settings" / "routing.json", cleaned)
    return cleaned


def route_entries_for_role(role: str) -> list[dict[str, str]]:
    """Expanded routing chain for a role: explicit entries plus Auto fallback.

    Each entry is {"provider": name, "model": model-or-"auto"}. Duplicate providers
    are allowed when they carry different models; identical provider+model pairs
    are deduplicated.
    """
    entries: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(provider: str, model: str) -> None:
        key = (provider, model or "auto")
        if key not in seen:
            seen.add(key)
            entries.append({"provider": provider, "model": key[1]})

    for entry in get_routing().get(role, []):
        provider = entry["provider"]
        if provider == "Auto":
            for name in auto_provider_order():
                add(name, "auto")
        else:
            add(provider, entry.get("model") or "auto")
    for name in auto_provider_order():
        add(name, "auto")
    return entries


def _attempt_route_keys(
    attempts: list[dict[str, Any]], role: str, start: int, default_role: str
) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for attempt in attempts[start:]:
        if str(attempt.get("role") or default_role) != role:
            continue
        provider = str(attempt.get("provider") or "")
        if not provider:
            continue
        keys.add((provider, str(attempt.get("route_model") or "auto")))
    return keys


def route_entry_for_role(task: dict[str, Any], role: str, cycle_key: str) -> dict[str, str] | None:
    """First untried routing entry (provider + model) for a role, or None."""
    configured = configured_providers()
    attempts = task.get("attempts") or []
    start = max(0, int(task.get(cycle_key, len(attempts)) or 0))
    tried = _attempt_route_keys(attempts, role, start, str(task.get("assigned_role") or ""))
    for entry in route_entries_for_role(role):
        if entry["provider"] in configured and (entry["provider"], entry["model"]) not in tried:
            return entry
    return None


def cleanup_provider_references(name: str) -> None:
    target = str(name or "").strip()
    if not target:
        return
    routing_path = DATA / "settings" / "routing.json"
    if routing_path.is_file():
        try:
            saved = json.loads(routing_path.read_text(encoding="utf-8"))
        except Exception:
            saved = {}
        if isinstance(saved, dict):
            changed = False
            for role in WORK_ROLES:
                raw = saved.get(role)
                if isinstance(raw, list):
                    def _entry_provider(item: Any) -> str:
                        if isinstance(item, dict):
                            return str(item.get("provider", "")).strip()
                        return str(item).strip()

                    if target in [_entry_provider(p) for p in raw]:
                        saved[role] = [p for p in raw if _entry_provider(p) != target]
                        changed = True
            if changed:
                atomic_json(routing_path, saved)
    if str(read_app_config().get("chat_provider") or "").strip() == target:
        save_app_config({"chat_provider": "Auto"})
    with STATE_LOCK:
        for summary in read_tasks():
            if str(summary.get("provider") or "") != target:
                continue
            if str(summary.get("status") or "") not in ("Ready", "Draft", "Waiting for capacity", "Rework needed"):
                continue
            task = read_task(str(summary.get("id", "")))
            if task:
                task["provider"] = "Automatic"
                write_task(task)


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


def read_tasks() -> list[dict[str, Any]]:
    ensure_data()
    tasks: list[dict[str, Any]] = []
    for p in (DATA / "tasks").glob("*.json"):
        try:
            tasks.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            continue
    tasks.sort(key=lambda t: str(t.get("created_at", "")), reverse=True)
    return tasks


def read_task(task_id: str) -> dict[str, Any] | None:
    if not re.fullmatch(r"T-\d{4,}", task_id):
        return None
    p = DATA / "tasks" / f"{task_id}.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def write_task(task: dict[str, Any]) -> None:
    ensure_data()
    with STATE_LOCK:
        task_path = DATA / "tasks" / f"{task['id']}.json"
        previous = read_task(str(task["id"]))
        atomic_json(task_path, task)
        timestamp = str(task.get("updated_at") or now_iso())
        if previous is None:
            status = str(task.get("status") or "Unknown")
            append_event(
                {
                    "at": timestamp,
                    "type": "task_created",
                    "task_id": task["id"],
                    "status": status,
                    "project_id": task.get("project_id") or "default",
                    "task_type": task.get("task_type") or "work",
                }
            )
            logger.info(
                "Task created task_id=%s status=%s project_id=%s",
                task["id"],
                status,
                task.get("project_id") or "default",
            )
        else:
            old_status = str(previous.get("status") or "Unknown")
            new_status = str(task.get("status") or "Unknown")
            if old_status != new_status:
                append_event(
                    {
                        "at": timestamp,
                        "type": "task_status_changed",
                        "task_id": task["id"],
                        "from": old_status,
                        "to": new_status,
                        "project_id": task.get("project_id") or "default",
                    }
                )
                logger.info(
                    "Task status changed task_id=%s from=%s to=%s project_id=%s",
                    task["id"],
                    old_status,
                    new_status,
                    task.get("project_id") or "default",
                )


def next_task_id() -> str:
    with STATE_LOCK:
        nums = []
        for t in read_tasks():
            m = re.fullmatch(r"T-(\d{4,})", str(t.get("id", "")))
            if m:
                nums.append(int(m.group(1)))
        nxt = (max(nums) if nums else 0) + 1
        return f"T-{nxt:04d}"


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


def is_project_doc_slug(slug: str) -> bool:
    return (
        slug in PROJECT_CONTEXT_FILES
        or slug == ROADMAP_DOC_SLUG
        or bool(PHASE_DOC_RE.fullmatch(slug))
    )


def project_doc_title(slug: str) -> str:
    if slug in PROJECT_CONTEXT_FILES:
        return PROJECT_CONTEXT_FILES[slug]
    if slug == ROADMAP_DOC_SLUG:
        return "Master plan (big plan / roadmap)"
    if PHASE_DOC_RE.fullmatch(slug):
        return f"Phase {slug.split('_', 1)[1]} plan"
    return slug


def project_phase_doc_slugs(project_id: str) -> list[str]:
    folder = DATA / "projects" / project_id / "context"
    if not folder.is_dir():
        return []
    return sorted(f.stem for f in folder.glob("phase_*.md") if PHASE_DOC_RE.fullmatch(f.stem))


def project_doc_slugs(project_id: str) -> list[str]:
    return list(PROJECT_CONTEXT_FILES) + [ROADMAP_DOC_SLUG] + project_phase_doc_slugs(project_id)


def project_context_file(project_id: str, slug: str) -> Path:
    if not is_project_doc_slug(slug) or not re.fullmatch(r"[a-z0-9-]+", project_id):
        raise ValueError("Unknown project context.")
    if not any(p.get("id") == project_id for p in read_projects()):
        raise ValueError("Project not found.")
    return DATA / "projects" / project_id / "context" / f"{slug}.md"


def ensure_project_context(project_id: str) -> None:
    for slug in PROJECT_CONTEXT_FILES:
        path = project_context_file(project_id, slug)
        if not path.exists():
            template = ROOT / "project_templates" / f"{slug}.md"
            if template.is_file():
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(template, path)
        history = path.parent / "history" / slug
        if path.is_file() and not any(history.glob("*.md")):
            history.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, history / f"{make_version_id()}.md")


def load_project_context(project_id: str, slug: str) -> str:
    if not is_project_doc_slug(slug):
        return ""
    ensure_project_context(project_id)
    p = project_context_file(project_id, slug)
    return p.read_text(encoding="utf-8").strip() if p.is_file() else ""


def save_project_context_document(project_id: str, slug: str, content: str) -> str | None:
    if not is_project_doc_slug(slug):
        raise ValueError("Unknown project context document.")
    ensure_project_context(project_id)
    p = project_context_file(project_id, slug)
    prev = p.read_text(encoding="utf-8") if p.is_file() else ""
    normalized = content.strip() + "\n"
    version_id: str | None = None
    if prev and prev != normalized:
        version_id = make_version_id()
        snap = p.parent / "history" / slug / f"{version_id}.md"
        snap.parent.mkdir(parents=True, exist_ok=True)
        snap.write_text(prev, encoding="utf-8")
    atomic_text(p, normalized)
    return version_id


def get_project_context_history(project_id: str, slug: str) -> list[dict[str, str]]:
    if not is_project_doc_slug(slug):
        return []
    ensure_project_context(project_id)
    folder = project_context_file(project_id, slug).parent / "history" / slug
    if not folder.is_dir():
        return []
    rows: list[dict[str, str]] = []
    for f in sorted(folder.glob("*.md"), reverse=True):
        preview = "Saved version could not be read."
        try:
            preview = re.sub(r"\s+", " ", f.read_text(encoding="utf-8")).strip()[:140]
        except Exception:
            pass
        rows.append(
            {
                "id": f.stem,
                "created_at": parse_version_timestamp(f.stem),
                "preview": preview,
            }
        )
    return rows


def project_doc_map_text(project_id: str, role: str, project: dict[str, Any]) -> str:
    auto = set(ROLE_AUTO_DOCS.get(role, []))
    lines: list[str] = []
    active = project.get("active_phase")
    if active:
        lines.append(f"Active phase: {active}")
    for slug in project_doc_slugs(project_id):
        try:
            size = len(load_project_context(project_id, slug))
        except Exception:
            size = 0
        if slug in PROJECT_DOC_DESCRIPTIONS:
            desc = PROJECT_DOC_DESCRIPTIONS[slug]
        elif PHASE_DOC_RE.fullmatch(slug):
            desc = f"Phase {slug.split('_', 1)[1]} plan."
        else:
            desc = ""
        tag = "auto-included above" if slug in auto else "on-request"
        lines.append(f"- {slug} — {project_doc_title(slug)} ({size} chars, {tag}): {desc}")
    lines.append(
        "Documents marked on-request are not loaded. When you need one, include "
        f'"info_requests": ["slug", ...] (max {INFO_REQUEST_MAX_DOCS} slugs) in your structured reply; '
        "the app will provide the content and let you continue."
    )
    return "\n".join(lines)


def project_context_for_role(role: str, task: dict[str, Any] | None = None) -> str:
    projects = read_projects()
    requested_project_id = str((task or {}).get("project_id") or "")
    project = next((p for p in projects if p.get("id") == requested_project_id), None)
    if not project and not requested_project_id:
        project = next((p for p in projects if p.get("id") == "default"), projects[0] if projects else None)
    pid = str(project.get("id") if project else "")
    auto_slugs = ROLE_AUTO_DOCS.get(role, ["technical_overview"])
    sections: list[tuple[str, str]] = [
        (project_doc_title(slug), load_project_context(pid, slug) if project else "")
        for slug in auto_slugs
    ]
    if role == "PM/PO":
        current = [
            t
            for t in read_tasks()
            if t.get("status") != "Canceled" and str(t.get("project_id") or "default") == pid
        ]
        board_lines = (
            [f"- {t['id']} | {t.get('status')} | {t.get('assigned_role')} | {t.get('title')}" for t in current[:40]]
            if current
            else ["- No tasks recorded."]
        )
        completed: list[str] = []
        for t in current:
            rel = t.get("latest_reply_file")
            if t.get("status") != "Done" or not rel:
                continue
            rp = (DATA / str(rel)).resolve()
            if not str(rp).startswith(str(DATA)):
                continue
            try:
                rep = json.loads(rp.read_text(encoding="utf-8"))
                excerpt = re.sub(r"\s+", " ", str(rep.get("text", ""))).strip()[:500]
            except Exception:
                excerpt = "Saved reply is unavailable."
            completed.append(f"- {t['id']} {t.get('title')}: {excerpt or 'No text reply.'}")
            if len(completed) == 8:
                break
        sections.append(("Current board (live)", "\n".join(board_lines)))
        sections.append(
            (
                "Recent completed agent responses (not proof of integration)",
                "\n".join(completed) if completed else "No completed agent responses yet.",
            )
        )

    if project:
        svc_summary = project_services_summary(project)
        svc_text = f"\n\nServices & hosting:\n{svc_summary}" if svc_summary else ""
        sections.append(
            (
                "Project",
                f"Name: {project.get('name', '')}\nRepository: {project.get('repository', '')}\n\n{project.get('context', '')}{svc_text}",
            )
        )
        mid = str((task or {}).get("module_id") or "")
        mod = next((m for m in project.get("modules", []) if m.get("id") == mid), None)
        if mod:
            sections.append(
                (
                    f"Sub-project: {mod.get('name')}",
                    f"Repository folder: {mod.get('path', '')}\n{mod.get('context', '')}",
                )
            )
        elif role == "PM/PO" and project.get("modules"):
            cat = "\n".join(
                f"- {m['id']}: {m['name']} ({m.get('path') or 'repository root'}) — {str(m.get('context', ''))[:500]}"
                for m in project["modules"]
            )
            sections.append(("Available sub-projects", cat))
        sections.append(("Project documents", project_doc_map_text(pid, role, project)))

    return "\n\n".join(f"## {title}\n{body[:6000]}" for title, body in sections if body and body.strip())


def chat_history_path(project_id: str) -> Path:
    safe_id = re.sub(r"[^a-z0-9-]+", "-", str(project_id or "default").lower()).strip("-") or "default"
    return DATA / "chat" / f"{safe_id}.json"


def read_chat_history(project_id: str = "default") -> list[dict[str, Any]]:
    ensure_data()
    p = chat_history_path(project_id)
    if not p.is_file():
        return []
    try:
        val = json.loads(p.read_text(encoding="utf-8"))
        return val if isinstance(val, list) else []
    except Exception:
        return []


def write_chat_history(messages: list[dict[str, Any]], project_id: str = "default") -> None:
    ensure_data()
    with STATE_LOCK:
        atomic_json(chat_history_path(project_id), messages)


def append_chat_notice(content: str, timestamp: str, task_id: str, project_id: str = "default") -> None:
    with STATE_LOCK:
        hist = read_chat_history(project_id)
        hist.append({"role": "assistant", "content": content, "at": timestamp, "task_id": task_id})
        write_chat_history(hist, project_id)


def read_task_replies(task_id: str) -> list[dict[str, Any]]:
    task = read_task(task_id)
    if not task:
        return []
    replies: list[dict[str, Any]] = []
    for att in task.get("attempts", []) or []:
        folder = str(att.get("provider", "")).lower().replace(" ", "")
        num = att.get("number")
        if not folder or not isinstance(num, int) or num < 1:
            continue
        rp = DATA / "replies" / task_id / folder / f"attempt-{num:03d}.json"
        if rp.is_file():
            try:
                replies.append(json.loads(rp.read_text(encoding="utf-8")))
            except Exception:
                continue
    return replies


def task_route_entry(task: dict[str, Any]) -> dict[str, str] | None:
    """Routing entry (provider + model) selected for a task, or None."""
    if task.get("workflow_phase") == "qa":
        return route_entry_for_role(task, "QA", "qa_routing_cycle_start")
    val = str(task.get("provider", ""))
    if val == "Antigravity":
        val = "Automatic"
    configured = configured_providers()
    if is_active_provider(val):
        return {"provider": val, "model": "auto"} if val in configured else None
    legacy = {"Gemini": "Gemini", "OpenRouter free agent": "OpenRouter"}
    role = str(task.get("assigned_role", ""))
    if role in legacy:
        p = legacy[role]
        return {"provider": p, "model": "auto"} if p in configured else None
    if val not in ("Automatic", "Auto"):
        return None
    attempts = task.get("attempts") or []
    start = max(0, int(task.get("routing_cycle_start") or 0))
    tried_names = {
        str(a.get("provider", ""))
        for a in attempts[start:]
        if str(a.get("role") or role) == role
    }
    if val == "Auto":
        p = select_auto_provider(tried_names)
        return {"provider": p, "model": "auto"} if p else None
    tried = _attempt_route_keys(attempts, role, start, role)
    for entry in route_entries_for_role(role or "PM/PO"):
        if entry["provider"] in configured and (entry["provider"], entry["model"]) not in tried:
            return entry
    return None


def task_provider(task: dict[str, Any]) -> str:
    entry = task_route_entry(task)
    return entry["provider"] if entry else ""


def provider_for_role(task: dict[str, Any], role: str, cycle_key: str) -> str:
    entry = route_entry_for_role(task, role, cycle_key)
    return entry["provider"] if entry else ""


def has_untried_alternate(task: dict[str, Any], failed_provider: str) -> bool:
    configured = configured_providers()
    attempts = task.get("attempts") or []
    role = str(task.get("assigned_role") or "PM/PO")
    cycle_key = "qa_routing_cycle_start" if role == "QA" else "routing_cycle_start"
    start = max(0, int(task.get(cycle_key, 0) or 0))
    tried = _attempt_route_keys(attempts, role, start, role)
    return any(
        entry["provider"] in configured and (entry["provider"], entry["model"]) not in tried
        for entry in route_entries_for_role(role)
    )


def task_repository_key(task: dict[str, Any]) -> str:
    if task.get("task_type") in ("pm_planning", "pm_escalation", "project_overview", "clarification_question", "design_review", "env_setup"):
        return ""
    role = str(task.get("assigned_role") or "")
    if role == "QA":
        role = str(task.get("qa_return_role") or "")
    if role not in ("Frontend developer", "Backend developer", "Documentation", "QA"):
        return ""
    project_id = str(task.get("project_id") or "default")
    project = next((p for p in read_projects() if p.get("id") == project_id), None)
    if not project or not project.get("repository"):
        return ""
    owner, repo = repository_parts(str(project["repository"]))
    return f"{owner.casefold()}/{repo.casefold()}"
