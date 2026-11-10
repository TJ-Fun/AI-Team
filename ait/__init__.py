"""Personal AI Team application package.

The modules in this package intentionally share a single flat namespace.
After all domain modules are imported, this package injects every public
name defined by them into each other's module globals. This mirrors the
original single-file ``server.py`` layout, so cross-module function calls
remain unchanged while the code is split into focused files.
"""

import types as _types
from pathlib import Path as _Path

from ait import common  # noqa: F401
from ait import chat  # noqa: F401
from ait import git_ops  # noqa: F401
from ait import http_handler  # noqa: F401
from ait import main  # noqa: F401
from ait import notifications  # noqa: F401
from ait import projects  # noqa: F401
from ait import prompts  # noqa: F401
from ait import routes_git  # noqa: F401
from ait import routes_settings  # noqa: F401
from ait import routes_projects  # noqa: F401
from ait import routes_tasks  # noqa: F401
from ait import pm_planning  # noqa: F401
from ait import provider_calls  # noqa: F401
from ait import provider_dispatch  # noqa: F401
from ait import qa_review  # noqa: F401
from ait import provider_registry  # noqa: F401
from ait import secrets  # noqa: F401
from ait import settings  # noqa: F401

_DOMAIN_MODULES = (
    settings,
    provider_registry,
    secrets,
    prompts,
    projects,
    git_ops,
    notifications,
    provider_calls,
    provider_dispatch,
    pm_planning,
    qa_review,
    routes_git,
    routes_projects,
    routes_settings,
    routes_tasks,
    chat,
    http_handler,
    main,
)


def _exported_names(module: _types.ModuleType):
    for name, value in vars(module).items():
        if name.startswith("_"):
            continue
        if isinstance(value, _types.ModuleType):
            continue
        if callable(value) or isinstance(
            value, (str, int, float, bool, list, dict, set, tuple, type(None), _Path)
        ):
            yield name, value


_NAMES: dict[str, object] = {}
for _mod in _DOMAIN_MODULES:
    for _name, _value in _exported_names(_mod):
        _NAMES.setdefault(_name, _value)

for _mod in _DOMAIN_MODULES:
    for _name, _value in _NAMES.items():
        _mod.__dict__.setdefault(_name, _value)
