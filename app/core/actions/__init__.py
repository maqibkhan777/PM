"""Actions package."""

from app.core.actions.base import BaseAction, ActionResult
from app.core.actions.types import (
    create_send_message_action,
    create_send_notification_action,
    create_transition_task_action,
    create_add_comment_action,
    create_create_task_action,
    create_update_task_action,
    create_assign_task_action,
    create_change_priority_action,
)
def __getattr__(name):
    if name in {"ActionEngine", "action_engine"}:
        from app.core.actions.engine import ActionEngine, action_engine

        globals()["ActionEngine"] = ActionEngine
        globals()["action_engine"] = action_engine
        return globals()[name]
    raise AttributeError(f"module 'app.core.actions' has no attribute '{name}'")

__all__ = [
    "BaseAction",
    "ActionResult",
    "create_send_message_action",
    "create_send_notification_action",
    "create_transition_task_action",
    "create_add_comment_action",
    "create_create_task_action",
    "create_update_task_action",
    "create_assign_task_action",
    "create_change_priority_action",
    "ActionEngine",
    "action_engine",
]

