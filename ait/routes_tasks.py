"""Task and PM-request API routes.

Thin dispatcher delegating to focused route modules
(routes_tasks_core, routes_tasks_pm, routes_tasks_ops), which share the
package flat namespace.
"""

from __future__ import annotations

from ait.common import *


def handle_tasks_get(handler, path, query) -> bool:
    return handle_tasks_core_get(handler, path, query)


def handle_tasks_post(handler, path, body) -> bool:
    if handle_tasks_core_post(handler, path, body):
        return True
    if handle_tasks_pm_post(handler, path, body):
        return True
    if handle_tasks_ops_post(handler, path, body):
        return True
    return handle_env_tasks_post(handler, path, body)


def handle_tasks_put(handler, path, body) -> bool:
    return handle_tasks_core_put(handler, path, body)


def handle_tasks_delete(handler, path) -> bool:
    return handle_tasks_core_delete(handler, path)
