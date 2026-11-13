"""GitHub repository contents wipe helpers."""

from __future__ import annotations

from ait.common import *


def _github_list_all(api_path: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    page = 1
    while True:
        sep = "&" if "?" in api_path else "?"
        batch = github_api(f"{api_path}{sep}per_page=100&page={page}")
        if not isinstance(batch, list):
            break
        items.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return items


def _ref_deleted(exc: ValueError) -> bool:
    return "Reference does not exist" in str(exc)


def github_delete_ref(owner: str, repo: str, ref: str) -> None:
    try:
        github_api(f"/repos/{owner}/{repo}/git/refs/{ref}", method="DELETE")
    except ValueError as exc:
        if _ref_deleted(exc):
            return
        raise


def github_create_empty_commit(owner: str, repo: str, default_branch: str) -> str:
    tree = github_api(f"/repos/{owner}/{repo}/git/trees", method="POST", payload={"tree": []})
    tree_sha = str((tree or {}).get("sha") or "")
    if not tree_sha:
        raise ValueError("GitHub did not return an empty tree sha.")
    commit = github_api(
        f"/repos/{owner}/{repo}/git/commits",
        method="POST",
        payload={"message": "Clean start", "tree": tree_sha, "parents": []},
    )
    commit_sha = str((commit or {}).get("sha") or "")
    if not commit_sha:
        raise ValueError("GitHub did not return the clean-start commit sha.")
    github_api(
        f"/repos/{owner}/{repo}/git/refs/heads/{default_branch}",
        method="PATCH",
        payload={"sha": commit_sha, "force": True},
    )
    return commit_sha


def github_wipe_repository_contents(repository: str) -> dict[str, Any]:
    owner, repo = canonical_github_repository(repository)
    meta = github_api(f"/repos/{owner}/{repo}")
    default_branch = str((meta or {}).get("default_branch") or "main")
    commit_sha = github_create_empty_commit(owner, repo, default_branch)

    errors: list[str] = []
    deleted_releases = 0
    deleted_branches = 0
    deleted_tags = 0

    for release in _github_list_all(f"/repos/{owner}/{repo}/releases"):
        release_id = release.get("id")
        if not release_id:
            continue
        try:
            github_api(f"/repos/{owner}/{repo}/releases/{release_id}", method="DELETE")
            deleted_releases += 1
        except ValueError as exc:
            errors.append(f"release {release_id}: {exc}")

    for branch in _github_list_all(f"/repos/{owner}/{repo}/branches"):
        name = str(branch.get("name") or "")
        if not name or name == default_branch:
            continue
        try:
            github_delete_ref(owner, repo, f"heads/{name}")
            deleted_branches += 1
        except ValueError as exc:
            errors.append(f"branch {name}: {exc}")

    for tag in _github_list_all(f"/repos/{owner}/{repo}/tags"):
        name = str(tag.get("name") or "")
        if not name:
            continue
        try:
            github_delete_ref(owner, repo, f"tags/{name}")
            deleted_tags += 1
        except ValueError as exc:
            errors.append(f"tag {name}: {exc}")

    return {
        "owner": owner,
        "repo": repo,
        "default_branch": default_branch,
        "commit": commit_sha,
        "deleted_releases": deleted_releases,
        "deleted_branches": deleted_branches,
        "deleted_tags": deleted_tags,
        "errors": errors,
    }
