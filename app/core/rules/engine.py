"""Rules Engine coordinator for PM Operations Agent."""

from typing import Any, Dict, List, Optional
from app.core.rules.base import BaseRule
from app.core.rules.builtin import (
    ActiveWorkRule,
    StaleTaskRule,
    OverdueRule,
    BlockedRule,
    ReopenedRule,
)
from app.core.events.base import BaseEvent
from app.core.actions.base import BaseAction
from app.database.repositories import RuleRepository
from app.database.connection import db_manager, DatabaseManager
from app.utils.logger import logger


class RulesEngine:
    """Evaluates events against active workflow rules and produces domain actions."""

    def __init__(self, manager: Optional[DatabaseManager] = None):
        self.mgr = manager or db_manager
        self.rule_repo = RuleRepository(self.mgr)
        self._rules: Dict[str, BaseRule] = {}
        self._register_default_rules()

    def _register_default_rules(self) -> None:
        """Register default built-in rules."""
        defaults = [
            ActiveWorkRule(),
            StaleTaskRule(),
            OverdueRule(),
            BlockedRule(),
            ReopenedRule(),
        ]
        for r in defaults:
            self.register_rule(r)

    def register_rule(self, rule: BaseRule) -> None:
        """Register a rule and persist its metadata to the database."""
        self._rules[rule.name] = rule
        # Sync with database
        db_rule = self.rule_repo.get_rule_by_name(rule.name)
        if db_rule:
            rule.enabled = bool(db_rule.get("enabled", 1))
            if db_rule.get("configuration"):
                rule.configuration.update(db_rule.get("configuration"))
        else:
            self.rule_repo.upsert_rule(
                name=rule.name,
                description=rule.description,
                enabled=rule.enabled,
                configuration=rule.configuration
            )
        logger.debug(f"Registered rule: {rule.name} (enabled={rule.enabled})")

    def get_rule(self, name: str) -> Optional[BaseRule]:
        return self._rules.get(name)

    def list_rules(self) -> List[Dict[str, Any]]:
        """Return list of all registered rules with their configuration and status."""
        return [
            {
                "name": r.name,
                "description": r.description,
                "enabled": r.enabled,
                "configuration": r.configuration
            }
            for r in self._rules.values()
        ]

    def set_rule_enabled(self, name: str, enabled: bool) -> bool:
        """Enable or disable a rule dynamically."""
        if name in self._rules:
            self._rules[name].enabled = enabled
            self.rule_repo.set_enabled(name, enabled)
            logger.info(f"Rule '{name}' enabled status set to: {enabled}")
            return True
        return False

    def evaluate_event(
        self,
        event: BaseEvent,
        context: Optional[Dict[str, Any]] = None
    ) -> List[BaseAction]:
        """Evaluate an incoming event against all enabled rules.

        Returns:
            List of generated BaseAction objects.
        """
        all_actions: List[BaseAction] = []
        logger.debug(f"Evaluating event {event.event_type} (task={event.task_key}) against {len(self._rules)} rules")

        for rule_name, rule in self._rules.items():
            if not rule.enabled:
                continue
            try:
                actions = rule.evaluate(event, context)
                if actions:
                    logger.info(f"Rule [{rule_name}] generated {len(actions)} action(s)")
                    all_actions.extend(actions)
            except Exception as e:
                logger.error(f"Error evaluating rule '{rule_name}' on event {event.event_type}: {e}", exc_info=True)

        return all_actions


# Global rules engine instance
rules_engine = RulesEngine()
