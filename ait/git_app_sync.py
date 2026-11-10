"""App checkout update, mirror sync, and service restart helpers."""

from __future__ import annotations

from ait.common import *


def update_app_checkout(repository_url: str, checkout_path: Path, token: str) -> str:
    """Replace the app checkout with a fresh clone of the configured repository.

    The remote repository is always the source of truth, so any existing
    checkout (including local modifications or untracked files) is discarded
    and replaced by a clean clone.
    """
    checkout_path = checkout_path.resolve()
    git_env = git_auth_environment(token)
    checkout_path.parent.mkdir(parents=True, exist_ok=True)
    staging_path = Path(tempfile.mkdtemp(prefix=f".{checkout_path.name}.clone-", dir=checkout_path.parent))
    command = ["git", "clone", "--progress", repository_url, str(staging_path)]

    def git_output(res: subprocess.CompletedProcess) -> str:
        return "\n".join(part.strip() for part in (res.stdout, res.stderr) if part and part.strip())

    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=300, env=git_env,
        )
    except PermissionError as exc:
        shutil.rmtree(staging_path, ignore_errors=True)
        raise ValueError(
            f"Permission denied accessing repository checkout '{checkout_path}'. "
            "Ensure the service user can write there and systemd allows the path."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        shutil.rmtree(staging_path, ignore_errors=True)
        detail = redact_git_output((exc.stderr or "") if isinstance(exc.stderr, str) else "", token).strip()
        raise ValueError(f"Git clone timed out after 300 seconds. {detail}".strip()) from exc
    except FileNotFoundError as exc:
        shutil.rmtree(staging_path, ignore_errors=True)
        raise ValueError("Git is not installed or is not available to the application service.") from exc

    output = redact_git_output(git_output(result), token)
    if result.returncode:
        shutil.rmtree(staging_path, ignore_errors=True)
        detail = output or f"git exited with status {result.returncode}"
        logger.error("App repository clone failed (exit=%s): %s", result.returncode, detail)
        raise ValueError(f"Git clone failed: {detail}")
    try:
        if checkout_path.is_symlink() or checkout_path.is_file():
            checkout_path.unlink()
        elif checkout_path.exists():
            shutil.rmtree(checkout_path)
        os.replace(staging_path, checkout_path)
    except Exception:
        shutil.rmtree(staging_path, ignore_errors=True)
        raise
    logger.info("App repository cloned (checkout=%s): %s", checkout_path, output or "no changes")
    return "clone"


MIRROR_IGNORED_DIRECTORIES = {".git", ".venv", "venv", "__pycache__", "node_modules"}
MIRROR_PROTECTED_ROOTS = {"data"}


def mirror_path_protected(relative: Path) -> bool:
    """Return True for destination paths that must survive a mirror sync."""
    parts = relative.parts
    if not parts:
        return False
    if parts[0] in MIRROR_PROTECTED_ROOTS:
        return True
    if any(part in MIRROR_IGNORED_DIRECTORIES for part in parts):
        return True
    name = parts[-1]
    return name.startswith(".env") or name.endswith(".log")


def sync_app_files(source: Path, destination: Path) -> tuple[int, int]:
    """Mirror repository files into the app directory, removing stale files.

    Every repository file is copied (overwriting existing files), and files or
    directories that only exist in the destination are deleted — except
    protected runtime data such as ``data/``, ``.env*``, logs, virtualenvs,
    caches, and ``.git``.
    """
    source = source.resolve(strict=True)
    destination = destination.resolve(strict=True)
    copied = deleted = 0
    source_files: set[Path] = set()
    source_dirs: set[Path] = set()

    def raise_walk_error(error: OSError) -> None:
        raise error

    for current, dirs, filenames in os.walk(source, followlinks=False, onerror=raise_walk_error):
        current_path = Path(current)
        dirs[:] = [
            name for name in dirs
            if name not in MIRROR_IGNORED_DIRECTORIES and not (current_path / name).is_symlink()
        ]
        relative_dir = current_path.relative_to(source)
        if relative_dir != Path("."):
            source_dirs.add(relative_dir)
        target_dir = destination / relative_dir
        if target_dir.is_symlink():
            raise ValueError(f"Refusing to write through a symbolic link in the app directory: {target_dir}")
        target_dir.mkdir(parents=True, exist_ok=True)
        if not target_dir.resolve().is_relative_to(destination):
            raise ValueError(f"Refusing to write outside the app working directory: {target_dir}")

        for filename in filenames:
            source_file = current_path / filename
            if source_file.is_symlink():
                continue
            relative_file = source_file.relative_to(source)
            source_files.add(relative_file)
            target_file = destination / relative_file
            if target_file.is_symlink():
                raise ValueError(f"Refusing to overwrite a symbolic link in the app directory: {target_file}")
            resolved_target = target_file.resolve(strict=False)
            if not resolved_target.is_relative_to(destination):
                raise ValueError(f"Refusing to write outside the app working directory: {target_file}")
            if target_file.exists() and not target_file.is_file():
                raise ValueError(f"Cannot replace non-file app path: {target_file}")

            fd, temporary_name = tempfile.mkstemp(prefix=f".{filename}.", dir=target_dir)
            os.close(fd)
            temporary_file = Path(temporary_name)
            try:
                shutil.copy2(source_file, temporary_file)
                os.replace(temporary_file, target_file)
            finally:
                temporary_file.unlink(missing_ok=True)
            copied += 1

    for current, dirs, filenames in os.walk(destination, followlinks=False, onerror=raise_walk_error):
        current_path = Path(current)
        dirs[:] = [
            name for name in dirs
            if name not in MIRROR_IGNORED_DIRECTORIES and not (current_path / name).is_symlink()
        ]
        for filename in filenames:
            target_file = current_path / filename
            relative_file = target_file.relative_to(destination)
            if relative_file in source_files or mirror_path_protected(relative_file):
                continue
            target_file.unlink()
            deleted += 1

    for current, dirs, _filenames in os.walk(destination, topdown=False, followlinks=False, onerror=raise_walk_error):
        current_path = Path(current)
        for name in dirs:
            candidate = current_path / name
            relative_dir = candidate.relative_to(destination)
            if relative_dir in source_dirs or mirror_path_protected(relative_dir):
                continue
            try:
                candidate.rmdir()
                deleted += 1
            except OSError:
                pass
    return copied, deleted


def restart_service_process() -> None:
    logger.info("Exiting app process; systemd will restart the service")
    os.kill(os.getpid(), signal.SIGTERM)
