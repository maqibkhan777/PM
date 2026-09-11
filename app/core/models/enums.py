"""Domain enums for PM Operations Agent."""

from enum import Enum


class TaskStatus(str, Enum):
    TO_DO = "To Do"
    IN_PROGRESS = "In Progress"
    BLOCKED = "Blocked"
    IN_REVIEW = "In Review"
    DONE = "Done"
    CLOSED = "Closed"
    UNKNOWN = "Unknown"

    @classmethod
    def from_str(cls, value: str) -> "TaskStatus":
        if not value:
            return cls.UNKNOWN
        val_clean = value.strip().lower()
        if val_clean in ("to do", "todo", "open", "backlog"):
            return cls.TO_DO
        elif val_clean in ("in progress", "doing", "active", "in development", "wip"):
            return cls.IN_PROGRESS
        elif val_clean in ("blocked", "impediment"):
            return cls.BLOCKED
        elif val_clean in ("in review", "review", "testing", "qa"):
            return cls.IN_REVIEW
        elif val_clean in ("done", "completed", "resolved", "finished"):
            return cls.DONE
        elif val_clean in ("closed", "archived"):
            return cls.CLOSED
        return cls.UNKNOWN


class TaskPriority(str, Enum):
    LOWEST = "Lowest"
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"
    HIGHEST = "Highest"
    UNKNOWN = "Unknown"


class ActionType(str, Enum):
    SEND_MESSAGE = "SendMessage"
    SEND_NOTIFICATION = "SendNotification"
    CREATE_TASK = "CreateTask"
    UPDATE_TASK = "UpdateTask"
    ASSIGN_TASK = "AssignTask"
    TRANSITION_TASK = "TransitionTask"
    ADD_COMMENT = "AddComment"
    CHANGE_PRIORITY = "ChangePriority"

    @classmethod
    def from_str(cls, value: str) -> Optional["ActionType"]:
        if not value:
            return None
        try:
            return cls(value)
        except ValueError:
            pass
        clean = value.strip().upper().replace(" ", "_").replace("-", "_")
        if clean in cls.__members__:
            return cls.__members__[clean]
        for member in cls:
            if member.value.lower() == value.strip().lower() or member.name.lower() == clean.lower():
                return member
        return None



class ActionStatus(str, Enum):
    REQUESTED = "REQUESTED"
    VALIDATED = "VALIDATED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXECUTING = "EXECUTING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    DRY_RUN_SIMULATED = "DRY_RUN_SIMULATED"
    ACTION_UNSUPPORTED = "ACTION_UNSUPPORTED"
    USER_MAPPING_REQUIRED = "USER_MAPPING_REQUIRED"
    SKIPPED = "SKIPPED"


class ApprovalClassification(str, Enum):
    AUTO = "AUTO"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    BLOCKED = "BLOCKED"


class SecurityLevel(str, Enum):
    READ = "READ"
    WRITE = "WRITE"
    DESTRUCTIVE = "DESTRUCTIVE"


class Capability(str, Enum):
    # Read capabilities
    READ_TASK = "READ_TASK"
    SEARCH_TASKS = "SEARCH_TASKS"
    GET_USER = "GET_USER"
    GET_PROJECTS = "GET_PROJECTS"

    # Write capabilities
    CREATE_TASK = "CREATE_TASK"
    UPDATE_TASK = "UPDATE_TASK"
    ASSIGN_TASK = "ASSIGN_TASK"
    TRANSITION_TASK = "TRANSITION_TASK"
    ADD_COMMENT = "ADD_COMMENT"
    CHANGE_PRIORITY = "CHANGE_PRIORITY"
    RESOLVE_USER = "RESOLVE_USER"
    SEND_DM = "SEND_DM"
    SEND_CHANNEL_MESSAGE = "SEND_CHANNEL_MESSAGE"
    SEND_NOTIFICATION = "SEND_NOTIFICATION"
    SEND_EMBED = "SEND_EMBED"

    # Destructive capabilities
    DELETE_TASK = "DELETE_TASK"
    BULK_DELETE = "BULK_DELETE"


# Mapping capability to SecurityLevel
CAPABILITY_SECURITY_MAP = {
    Capability.READ_TASK: SecurityLevel.READ,
    Capability.SEARCH_TASKS: SecurityLevel.READ,
    Capability.GET_USER: SecurityLevel.READ,
    Capability.GET_PROJECTS: SecurityLevel.READ,
    Capability.CREATE_TASK: SecurityLevel.WRITE,
    Capability.UPDATE_TASK: SecurityLevel.WRITE,
    Capability.ASSIGN_TASK: SecurityLevel.WRITE,
    Capability.TRANSITION_TASK: SecurityLevel.WRITE,
    Capability.ADD_COMMENT: SecurityLevel.WRITE,
    Capability.CHANGE_PRIORITY: SecurityLevel.WRITE,
    Capability.RESOLVE_USER: SecurityLevel.READ,
    Capability.SEND_DM: SecurityLevel.WRITE,
    Capability.SEND_CHANNEL_MESSAGE: SecurityLevel.WRITE,
    Capability.SEND_NOTIFICATION: SecurityLevel.WRITE,
    Capability.SEND_EMBED: SecurityLevel.WRITE,
    Capability.DELETE_TASK: SecurityLevel.DESTRUCTIVE,
    Capability.BULK_DELETE: SecurityLevel.DESTRUCTIVE,
}


class EventProcessingStatus(str, Enum):
    RECEIVED = "RECEIVED"
    PROCESSING = "PROCESSING"
    PROCESSED = "PROCESSED"
    FAILED = "FAILED"
    RETRY_PENDING = "RETRY_PENDING"
