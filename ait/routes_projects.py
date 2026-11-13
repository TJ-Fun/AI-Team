"""Project and project-context API routes."""

from __future__ import annotations

from ait.common import *


def handle_projects_get(handler, path, query) -> bool:
    if path == "/api/projects":
        handler.send_json({"projects": read_projects()})
        return True

    m_gh_files = re.fullmatch(r"/api/projects/([a-z0-9-]+)/github-files", path)
    if m_gh_files:
        pid = m_gh_files.group(1)
        try:
            f_q = query.get("file", [""])[0]
            p_q = query.get("path", [""])[0]
            if f_q:
                handler.send_json(github_read_text_file(pid, f_q))
                return True
            handler.send_json(github_file_listing(pid, p_q))
            return True
        except ValueError as exc:
            handler.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return True

    m_local_files = re.fullmatch(r"/api/projects/([a-z0-9-]+)/local-files", path)
    if m_local_files:
        try:
            project_root = local_project_root(m_local_files.group(1))
            file_path = query.get("file", [""])[0]
            folder_path = query.get("path", [""])[0]
            if file_path:
                handler.send_json(read_local_text_file(project_root, file_path))
                return True
            handler.send_json(local_file_listing(project_root, folder_path))
            return True
        except ValueError as exc:
            handler.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return True

    m_proj_hist = re.fullmatch(r"/api/projects/([a-z0-9-]+)/context-history", path)
    if m_proj_hist:
        handler.send_json({"history": project_background_history(m_proj_hist.group(1))})
        return True

    m_project_context = re.fullmatch(r"/api/projects/([a-z0-9-]+)/context-documents", path)
    if m_project_context:
        project_id = m_project_context.group(1)
        if not any(p.get("id") == project_id for p in read_projects()):
            handler.send_json({"error": "Project not found."}, HTTPStatus.NOT_FOUND)
            return True
        ensure_project_context(project_id)
        docs = [
            {"id": slug, "title": project_doc_title(slug), "content": load_project_context(project_id, slug)}
            for slug in project_doc_slugs(project_id)
        ]
        handler.send_json({"documents": docs})
        return True

    m_ctx_hist = re.fullmatch(r"/api/projects/([a-z0-9-]+)/context-documents/([a-z_]+)/history", path)
    if m_ctx_hist:
        project_id, slug = m_ctx_hist.groups()
        if not is_project_doc_slug(slug):
            handler.send_json({"error": "Project document not found."}, HTTPStatus.NOT_FOUND)
            return True
        if not any(p.get("id") == project_id for p in read_projects()):
            handler.send_json({"error": "Project not found."}, HTTPStatus.NOT_FOUND)
            return True
        handler.send_json({"history": get_project_context_history(project_id, slug)})
        return True

    return False


def handle_projects_post(handler, path, body) -> bool:
    m_clear_tasks = re.fullmatch(r"/api/projects/([a-z0-9-]+)/clear-tasks", path)
    if m_clear_tasks:
        if body.get("confirm") is not True:
            handler.send_json({"error": "Confirmation required."}, HTTPStatus.BAD_REQUEST)
            return True
        try:
            handler.send_json(clear_project_tasks(m_clear_tasks.group(1)))
        except ProjectBusyError as exc:
            handler.send_json({"error": str(exc)}, HTTPStatus.CONFLICT)
        except ValueError as exc:
            handler.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        return True

    m_ctx_res = re.fullmatch(r"/api/projects/([a-z0-9-]+)/context-documents/([a-z_]+)/restore", path)
    if m_ctx_res:
        pid, slug = m_ctx_res.groups()
        vid = str(body.get("snapshot_id", ""))
        if not is_project_doc_slug(slug):
            handler.send_json({"error": "Project document not found."}, HTTPStatus.NOT_FOUND)
            return True
        if not any(p.get("id") == pid for p in read_projects()):
            handler.send_json({"error": "Project not found."}, HTTPStatus.NOT_FOUND)
            return True
        if not re.fullmatch(r"\d{8}T\d{12}Z", vid):
            handler.send_json({"error": "Choose a valid saved version."}, HTTPStatus.BAD_REQUEST)
            return True
        snap = project_context_file(pid, slug).parent / "history" / slug / f"{vid}.md"
        if not snap.is_file():
            handler.send_json({"error": "Saved version not found."}, HTTPStatus.NOT_FOUND)
            return True
        txt = snap.read_text(encoding="utf-8")
        new_snap = save_project_context_document(pid, slug, txt)
        append_event(
            {
                "at": now_iso(),
                "type": "project_context_restored",
                "project_id": pid,
                "document": slug,
                "restored_snapshot": vid,
                "snapshot": new_snap,
            }
        )
        handler.send_json({"saved": True, "id": slug, "title": PROJECT_CONTEXT_FILES[slug], "content": txt.strip()})
        return True

    m_proj_res = re.fullmatch(r"/api/projects/([a-z0-9-]+)/restore-context", path)
    if m_proj_res:
        pid = m_proj_res.group(1)
        vid = str(body.get("version_id", ""))
        if not re.fullmatch(r"\d{8}T\d{12}Z", vid):
            handler.send_json({"error": "Choose a valid saved project version."}, HTTPStatus.BAD_REQUEST)
            return True
        snap = DATA / "projects" / pid / "history" / f"{vid}.md"
        if not snap.is_file():
            handler.send_json({"error": "Saved project version not found."}, HTTPStatus.NOT_FOUND)
            return True
        restored = snap.read_text(encoding="utf-8")
        projs = read_projects()
        proj = next((p for p in projs if p.get("id") == pid), None)
        if not proj:
            handler.send_json({"error": "Project not found."}, HTTPStatus.NOT_FOUND)
            return True
        proj["context"] = restored
        save_projects(projs)
        handler.send_json({"restored": True, "context": restored})
        return True

    return False


def handle_projects_put(handler, path, body) -> bool:
    m_ctx = re.fullmatch(r"/api/projects/([a-z0-9-]+)/context-documents/([a-z_]+)", path)
    if m_ctx:
        pid, slug = m_ctx.groups()
        if not is_project_doc_slug(slug):
            handler.send_json({"error": "Project document not found."}, HTTPStatus.NOT_FOUND)
            return True
        if not any(p.get("id") == pid for p in read_projects()):
            handler.send_json({"error": "Project not found."}, HTTPStatus.NOT_FOUND)
            return True
        content = body.get("content")
        if not isinstance(content, str) or not content.strip() or len(content) > 20000:
            handler.send_json({"error": "Project document must be non-empty and under 20,000 characters."}, HTTPStatus.BAD_REQUEST)
            return True
        snap = save_project_context_document(pid, slug, content)
        append_event(
            {"at": now_iso(), "type": "project_context_saved", "project_id": pid, "document": slug, "snapshot": snap}
        )
        handler.send_json(
            {"saved": True, "project_id": pid, "id": slug, "title": PROJECT_CONTEXT_FILES[slug], "content": content.strip(), "snapshot": snap}
        )
        return True

    if path == "/api/projects":
        try:
            projects = save_projects(body.get("projects"))
            append_event({"at": now_iso(), "type": "projects_updated", "count": len(projects)})
            handler.send_json({"projects": projects})
            return True
        except ValueError as exc:
            handler.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return True

    return False


def handle_projects_delete(handler, path) -> bool:
    m_ctx_del = re.fullmatch(
        r"/api/projects/([a-z0-9-]+)/context-documents/([a-z_]+)/history/(\d{8}T\d{12}Z)", path
    )
    if m_ctx_del:
        pid, slug, vid = m_ctx_del.groups()
        if not is_project_doc_slug(slug) or not any(p.get("id") == pid for p in read_projects()):
            handler.send_json({"error": "Saved version not found."}, HTTPStatus.NOT_FOUND)
            return True
        snap = project_context_file(pid, slug).parent / "history" / slug / f"{vid}.md"
        if not snap.is_file():
            handler.send_json({"error": "Saved version not found."}, HTTPStatus.NOT_FOUND)
            return True
        snap.unlink()
        handler.send_json({"deleted": True, "project_id": pid, "id": slug, "deleted_version": vid})
        return True

    m_proj_del = re.fullmatch(r"/api/projects/([a-z0-9-]+)/context-history/(\d{8}T\d{12}Z)", path)
    if m_proj_del:
        pid, vid = m_proj_del.group(1), m_proj_del.group(2)
        snap = DATA / "projects" / pid / "history" / f"{vid}.md"
        if not snap.is_file():
            handler.send_json({"error": "Saved project version not found."}, HTTPStatus.NOT_FOUND)
            return True
        snap.unlink()
        handler.send_json({"deleted": True, "project_id": pid, "deleted_version": vid})
        return True

    return False

