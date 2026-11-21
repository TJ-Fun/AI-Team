"""Per-project context documents: slugs, files, versioned history, role assembly."""

from __future__ import annotations

from ait.common import *


# --- Project context documents ---
def is_project_doc_slug(slug: str) -> bool:
    return (
        slug in PROJECT_CONTEXT_FILES
        or slug == ROADMAP_DOC_SLUG
        or slug == LEGACY_ROADMAP_SLUG
        or slug == ROADMAP_TRACKER_DOC_SLUG
        or bool(PHASE_DOC_RE.fullmatch(slug))
        or bool(PHASE_TRACKER_DOC_RE.fullmatch(slug))
    )


def project_doc_title(slug: str) -> str:
    if slug in PROJECT_CONTEXT_FILES:
        return PROJECT_CONTEXT_FILES[slug]
    if slug == ROADMAP_DOC_SLUG or slug == LEGACY_ROADMAP_SLUG:
        return "Roadmap"
    if slug == ROADMAP_TRACKER_DOC_SLUG:
        return "Roadmap tracker"
    tracker_match = PHASE_TRACKER_DOC_RE.fullmatch(slug)
    if tracker_match:
        return f"Phase {slug.split('_', 1)[1].removesuffix('_tracker')} tracker"
    if PHASE_DOC_RE.fullmatch(slug):
        return f"Phase {slug.split('_', 1)[1]} plan"
    return slug


def project_phase_doc_slugs(project_id: str) -> list[str]:
    folder = DATA / "projects" / project_id / "context"
    if not folder.is_dir():
        return []
    return sorted(f.stem for f in folder.glob("phase_*.md") if PHASE_DOC_RE.fullmatch(f.stem))


def project_phase_tracker_slugs(project_id: str) -> list[str]:
    folder = DATA / "projects" / project_id / "context"
    if not folder.is_dir():
        return []
    return sorted(f.stem for f in folder.glob("phase_*_tracker.md") if PHASE_TRACKER_DOC_RE.fullmatch(f.stem))


def project_doc_slugs(project_id: str) -> list[str]:
    return (
        list(PROJECT_CONTEXT_FILES)
        + [ROADMAP_DOC_SLUG, ROADMAP_TRACKER_DOC_SLUG]
        + project_phase_doc_slugs(project_id)
        + project_phase_tracker_slugs(project_id)
    )


def project_context_file(project_id: str, slug: str) -> Path:
    if slug == LEGACY_ROADMAP_SLUG:
        slug = ROADMAP_DOC_SLUG
    if not is_project_doc_slug(slug) or not re.fullmatch(r"[a-z0-9-]+", project_id):
        raise ValueError("Unknown project context.")
    if not any(p.get("id") == project_id for p in read_projects()):
        raise ValueError("Project not found.")
    return DATA / "projects" / project_id / "context" / f"{slug}.md"


def ensure_project_context(project_id: str) -> None:
    context_dir = DATA / "projects" / project_id / "context"
    legacy_roadmap = context_dir / f"{LEGACY_ROADMAP_SLUG}.md"
    new_roadmap = context_dir / f"{ROADMAP_DOC_SLUG}.md"
    if legacy_roadmap.is_file() and not new_roadmap.exists():
        legacy_roadmap.rename(new_roadmap)
        legacy_hist = context_dir / "history" / LEGACY_ROADMAP_SLUG
        new_hist = context_dir / "history" / ROADMAP_DOC_SLUG
        if legacy_hist.is_dir() and not new_hist.exists():
            legacy_hist.rename(new_hist)
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
    if slug == LEGACY_ROADMAP_SLUG:
        slug = ROADMAP_DOC_SLUG
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
    if slug == LEGACY_ROADMAP_SLUG:
        slug = ROADMAP_DOC_SLUG
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
        try:
            mtime = project_context_file(project_id, slug).stat().st_mtime
            updated = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")
        except Exception:
            updated = ""
        if slug in PROJECT_DOC_DESCRIPTIONS:
            desc = PROJECT_DOC_DESCRIPTIONS[slug]
        elif PHASE_DOC_RE.fullmatch(slug):
            desc = f"Phase {slug.split('_', 1)[1]} plan."
        else:
            desc = ""
        tag = "auto-included above" if slug in auto else "on-request"
        freshness = f", updated {updated}" if updated else ""
        lines.append(f"- {slug} — {project_doc_title(slug)} ({size} chars{freshness}, {tag}): {desc}")
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
