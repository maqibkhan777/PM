"""Deterministic Task Nature Classifier for Phase B Historical Intelligence.

Classifies Jira issues into 17 standard task nature categories using a transparent,
explainable rule precedence hierarchy over available Jira fields:
issue_type, components, labels, summary, description, and workflow status.

Strictly deterministic. No LLMs or fuzzy role-based guessing.
"""

import re
from typing import Any, Dict, List, Optional, Tuple

from app.core.intelligence.models import TaskNature, TaskNatureClassification
from app.core.models.performance import ConfidenceLevel


class TaskNatureClassifier:
    """Deterministic, transparent task nature classification engine."""

    # Explicit Issue Type mappings
    ISSUE_TYPE_MAP = {
        "bug": TaskNature.BUG_FIX,
        "defect": TaskNature.BUG_FIX,
        "problem": TaskNature.BUG_FIX,
        "incident": TaskNature.CUSTOMER_SUPPORT,
        "service request": TaskNature.CUSTOMER_SUPPORT,
        "support": TaskNature.CUSTOMER_SUPPORT,
        "research": TaskNature.RESEARCH,
        "spike": TaskNature.RESEARCH,
        "design": TaskNature.DESIGN,
        "documentation": TaskNature.DOCUMENTATION,
        "qa": TaskNature.QA_TESTING,
        "test": TaskNature.QA_TESTING,
        "test execution": TaskNature.QA_TESTING,
        "story": None,  # Evaluate via keywords/components
        "task": None,   # Evaluate via keywords/components
        "sub-task": None,
        "subtask": None,
        "improvement": TaskNature.ENHANCEMENT,
        "new feature": TaskNature.DEVELOPMENT,
        "epic": None,
    }

    # Component & Label exact tag mappings (normalized lowercase)
    TAG_MAP = {
        "seo": TaskNature.SEO,
        "search-engine": TaskNature.SEO,
        "backlinks": TaskNature.SEO,
        "meta-tags": TaskNature.SEO,
        "content": TaskNature.CONTENT,
        "copywriting": TaskNature.CONTENT,
        "blog": TaskNature.CONTENT,
        "article": TaskNature.CONTENT,
        "design": TaskNature.DESIGN,
        "ui": TaskNature.DESIGN,
        "ux": TaskNature.DESIGN,
        "figma": TaskNature.DESIGN,
        "graphics": TaskNature.DESIGN,
        "banner": TaskNature.DESIGN,
        "qa": TaskNature.QA_TESTING,
        "testing": TaskNature.QA_TESTING,
        "automation": TaskNature.QA_TESTING,
        "test-cases": TaskNature.QA_TESTING,
        "documentation": TaskNature.DOCUMENTATION,
        "docs": TaskNature.DOCUMENTATION,
        "readme": TaskNature.DOCUMENTATION,
        "support": TaskNature.CUSTOMER_SUPPORT,
        "customer-support": TaskNature.CUSTOMER_SUPPORT,
        "client-support": TaskNature.CUSTOMER_SUPPORT,
        "helpdesk": TaskNature.CUSTOMER_SUPPORT,
        "deployment": TaskNature.DEPLOYMENT,
        "devops": TaskNature.DEPLOYMENT,
        "release": TaskNature.DEPLOYMENT,
        "ci-cd": TaskNature.DEPLOYMENT,
        "pipeline": TaskNature.DEPLOYMENT,
        "docker": TaskNature.DEPLOYMENT,
        "research": TaskNature.RESEARCH,
        "spike": TaskNature.RESEARCH,
        "poc": TaskNature.RESEARCH,
        "investigation": TaskNature.RESEARCH,
        "maintenance": TaskNature.MAINTENANCE,
        "refactor": TaskNature.MAINTENANCE,
        "tech-debt": TaskNature.MAINTENANCE,
        "cleanup": TaskNature.MAINTENANCE,
        "upgrade": TaskNature.MAINTENANCE,
        "enhancement": TaskNature.ENHANCEMENT,
        "optimization": TaskNature.ENHANCEMENT,
        "speed": TaskNature.ENHANCEMENT,
        "review": TaskNature.REVIEW,
        "code-review": TaskNature.REVIEW,
        "audit": TaskNature.REVIEW,
        "business-analysis": TaskNature.BUSINESS_ANALYSIS,
        "ba": TaskNature.BUSINESS_ANALYSIS,
        "requirements": TaskNature.BUSINESS_ANALYSIS,
        "spec": TaskNature.BUSINESS_ANALYSIS,
    }

    # Keyword patterns for Summary & Description (in order of priority)
    KEYWORD_RULES: List[Tuple[TaskNature, List[str], str]] = [
        (
            TaskNature.BUG_FIX,
            [r"\bfix\b", r"\bbug\b", r"\berror\b", r"\bbroken\b", r"\bcrash\b", r"\bpatch\b", r"\bresolve\b", r"\bhotfix\b", r"\bdefect\b", r"\bglitch\b", r"\bnot working\b"],
            "summary_keyword_bugfix"
        ),
        (
            TaskNature.QA_TESTING,
            [r"\btest\b", r"\btesting\b", r"\bqa\b", r"\btest cases?\b", r"\bregression\b", r"\be2e\b", r"\bverify\b", r"\bvalidate\b", r"\bautomation\b"],
            "summary_keyword_qa"
        ),
        (
            TaskNature.SEO,
            [r"\bseo\b", r"\bbacklinks?\b", r"\bmeta tags?\b", r"\bsitemap\b", r"\bschema markup\b", r"\bsearch ranking\b", r"\bkeyword research\b", r"\balt tags?\b", r"\brobots\.txt\b"],
            "summary_keyword_seo"
        ),
        (
            TaskNature.CONTENT,
            [r"\bcontent\b", r"\bblog\b", r"\barticle\b", r"\bcopywriting\b", r"\bproofread\b", r"\bwriteup\b", r"\bpublish post\b", r"\blanding page copy\b"],
            "summary_keyword_content"
        ),
        (
            TaskNature.DESIGN,
            [r"\bdesign\b", r"\bui\b", r"\bux\b", r"\bfigma\b", r"\bmockups?\b", r"\bwireframes?\b", r"\bprototypes?\b", r"\bbanner\b", r"\bgraphics?\b", r"\blogo\b", r"\biconography\b"],
            "summary_keyword_design"
        ),
        (
            TaskNature.RESEARCH,
            [r"\bresearch\b", r"\bspike\b", r"\binvestigate\b", r"\bexplore\b", r"\bpoc\b", r"\bproof of concept\b", r"\bfeasibility\b", r"\bevaluate\b"],
            "summary_keyword_research"
        ),
        (
            TaskNature.BUSINESS_ANALYSIS,
            [r"\bspecification\b", r"\bspecs?\b", r"\brequirements?\b", r"\buser story\b", r"\bprd\b", r"\bacceptance criteria\b", r"\bscope of work\b", r"\bsow\b"],
            "summary_keyword_ba"
        ),
        (
            TaskNature.DOCUMENTATION,
            [r"\bdocumentation\b", r"\bdocs?\b", r"\breadme\b", r"\buser guide\b", r"\bmanual\b", r"\bwiki\b", r"\bchangelog\b", r"\bapi docs?\b"],
            "summary_keyword_doc"
        ),
        (
            TaskNature.DEPLOYMENT,
            [r"\bdeploy\b", r"\bdeployment\b", r"\brelease\b", r"\bci/cd\b", r"\bpipeline\b", r"\bdocker\b", r"\bkubernetes\b", r"\bserver setup\b", r"\bstaging\b", r"\bproduction build\b"],
            "summary_keyword_deploy"
        ),
        (
            TaskNature.REVIEW,
            [r"\bcode review\b", r"\bpr review\b", r"\baudit\b", r"\bfeedback review\b", r"\bdesign review\b", r"\bpeer review\b"],
            "summary_keyword_review"
        ),
        (
            TaskNature.CUSTOMER_SUPPORT,
            [r"\bsupport\b", r"\bcustomer\b", r"\bclient request\b", r"\buser ticket\b", r"\binquiry\b", r"\buser reported\b", r"\bhelpdesk\b", r"\btroubleshoot\b"],
            "summary_keyword_support"
        ),
        (
            TaskNature.COMMUNICATION,
            [r"\bmeeting\b", r"\bstandup\b", r"\bsync\b", r"\bclient call\b", r"\bdiscussion\b", r"\bpresentation\b"],
            "summary_keyword_comm"
        ),
        (
            TaskNature.MAINTENANCE,
            [r"\brefactor\b", r"\bclean up\b", r"\bcleanup\b", r"\bmaintenance\b", r"\btech debt\b", r"\bupgrade dependency\b", r"\bdeprecate\b", r"\bmigrate\b"],
            "summary_keyword_maint"
        ),
        (
            TaskNature.ENHANCEMENT,
            [r"\benhance\b", r"\benhancement\b", r"\boptimize\b", r"\boptimization\b", r"\bspeed up\b", r"\bimprove\b", r"\brefine\b"],
            "summary_keyword_enhance"
        ),
        (
            TaskNature.DEVELOPMENT,
            [r"\bfeature\b", r"\bimplement\b", r"\bcreate\b", r"\bbuild\b", r"\badd\b", r"\bintegrate\b", r"\bdevelop\b", r"\bendpoint\b", r"\bapi\b", r"\bhook\b", r"\bplugin\b", r"\bcomponent\b"],
            "summary_keyword_dev"
        ),
    ]

    @classmethod
    def classify_issue(
        cls,
        issue_data: Optional[Any] = None,
        *,
        issue_key: Optional[str] = None,
        issue_type: Optional[str] = None,
        summary: Optional[str] = None,
        description: Optional[str] = None,
        components: Optional[List[str]] = None,
        labels: Optional[List[str]] = None,
        **kwargs,
    ) -> TaskNatureClassification:
        """Classify a single Jira issue into a TaskNature."""
        data_dict: Dict[str, Any] = {}
        if issue_data is not None:
            if isinstance(issue_data, dict):
                data_dict = issue_data
            elif hasattr(issue_data, "model_dump"):
                data_dict = issue_data.model_dump()
            elif hasattr(issue_data, "__dict__"):
                data_dict = issue_data.__dict__

        resolved_key = (
            issue_key
            or data_dict.get("issue_key")
            or data_dict.get("key")
            or data_dict.get("jira_issue_key")
            or "UNKNOWN"
        )
        resolved_type = str(
            issue_type
            or data_dict.get("issue_type")
            or data_dict.get("issuetype")
            or ""
        ).strip().lower()
        resolved_summary = str(
            summary
            or data_dict.get("summary")
            or ""
        ).strip()
        resolved_desc = str(
            description
            or data_dict.get("description")
            or ""
        ).strip()

        raw_components = components if components is not None else data_dict.get("components", [])
        raw_labels = labels if labels is not None else data_dict.get("labels", [])

        # Parse stringified JSON or comma-separated if needed
        parsed_components: List[str] = []
        if isinstance(raw_components, list):
            parsed_components = [str(c).strip() for c in raw_components if str(c).strip()]
        elif isinstance(raw_components, str):
            try:
                import json
                parsed = json.loads(raw_components)
                if isinstance(parsed, list):
                    parsed_components = [str(c).strip() for c in parsed if str(c).strip()]
                else:
                    parsed_components = [raw_components.strip()]
            except Exception:
                parsed_components = [c.strip() for c in raw_components.split(",") if c.strip()]

        parsed_labels: List[str] = []
        if isinstance(raw_labels, list):
            parsed_labels = [str(l).strip() for l in raw_labels if str(l).strip()]
        elif isinstance(raw_labels, str):
            try:
                import json
                parsed = json.loads(raw_labels)
                if isinstance(parsed, list):
                    parsed_labels = [str(l).strip() for l in parsed if str(l).strip()]
                else:
                    parsed_labels = [raw_labels.strip()]
            except Exception:
                parsed_labels = [l.strip() for l in raw_labels.split(",") if l.strip()]

        combined_text = f"{resolved_summary} {resolved_desc}".lower()

        # 1. Rule Level 1: Explicit Issue Type
        if resolved_type in cls.ISSUE_TYPE_MAP and cls.ISSUE_TYPE_MAP[resolved_type] is not None:
            nature = cls.ISSUE_TYPE_MAP[resolved_type]
            return TaskNatureClassification(
                issue_key=resolved_key,
                task_nature=nature,
                classification_source="issue_type",
                classification_confidence=ConfidenceLevel.HIGH,
                matched_rule=f"issue_type_equals_{resolved_type}",
                components=parsed_components,
                labels=parsed_labels,
            )

        # 2. Rule Level 2: Component & Label Tags
        for tag in parsed_labels + parsed_components:
            clean_tag = tag.strip().lower()
            if clean_tag in cls.TAG_MAP:
                return TaskNatureClassification(
                    issue_key=resolved_key,
                    task_nature=cls.TAG_MAP[clean_tag],
                    classification_source="label" if tag in parsed_labels else "component",
                    classification_confidence=ConfidenceLevel.HIGH,
                    matched_rule=f"tag_equals_{clean_tag}",
                    components=parsed_components,
                    labels=parsed_labels,
                )

        # 3. Rule Level 3: Summary & Description Keyword Match
        for nature, patterns, rule_name in cls.KEYWORD_RULES:
            for pattern_str in patterns:
                if re.search(pattern_str, combined_text, re.IGNORECASE):
                    source = "summary_keyword" if re.search(pattern_str, resolved_summary, re.IGNORECASE) else "description_keyword"
                    return TaskNatureClassification(
                        issue_key=resolved_key,
                        task_nature=nature,
                        classification_source=source,
                        classification_confidence=ConfidenceLevel.MEDIUM,
                        matched_rule=f"{rule_name}_{pattern_str}",
                        components=parsed_components,
                        labels=parsed_labels,
                    )

        # 4. Fallback Level 4: UNKNOWN
        return TaskNatureClassification(
            issue_key=resolved_key,
            task_nature=TaskNature.UNKNOWN,
            classification_source="fallback",
            classification_confidence=ConfidenceLevel.LOW,
            matched_rule="fallback_default_unknown",
            components=parsed_components,
            labels=parsed_labels,
        )

        labels: List[str] = []
        if isinstance(raw_labels, list):
            labels = [str(l).strip() for l in raw_labels if str(l).strip()]
        elif isinstance(raw_labels, str):
            try:
                import json
                parsed = json.loads(raw_labels)
                if isinstance(parsed, list):
                    labels = [str(l).strip() for l in parsed if str(l).strip()]
                else:
                    labels = [raw_labels.strip()]
            except Exception:
                labels = [l.strip() for l in raw_labels.split(",") if l.strip()]

        combined_text = f"{summary} {desc}".lower()

        # 1. Rule Level 1: Explicit Issue Type
        if issue_type in cls.ISSUE_TYPE_MAP and cls.ISSUE_TYPE_MAP[issue_type] is not None:
            nature = cls.ISSUE_TYPE_MAP[issue_type]
            return TaskNatureClassification(
                issue_key=issue_key,
                task_nature=nature,
                classification_source="issue_type",
                classification_confidence=ConfidenceLevel.HIGH,
                matched_rule=f"issue_type_equals_{issue_type}",
                components=components,
                labels=labels,
            )

        # 2. Rule Level 2: Component & Label Tags
        for tag in labels + components:
            clean_tag = tag.strip().lower().replace("_", "-").replace(" ", "-")
            if clean_tag in cls.TAG_MAP:
                return TaskNatureClassification(
                    issue_key=issue_key,
                    task_nature=cls.TAG_MAP[clean_tag],
                    classification_source="component_or_label",
                    classification_confidence=ConfidenceLevel.HIGH,
                    matched_rule=f"tag_equals_{clean_tag}",
                    components=components,
                    labels=labels,
                )

        # 3. Rule Level 3: Summary & Description Keywords
        for nature, patterns, rule_name in cls.KEYWORD_RULES:
            for pat in patterns:
                if re.search(pat, combined_text, re.IGNORECASE):
                    # Higher confidence if matched in summary vs description
                    in_summary = bool(re.search(pat, summary.lower(), re.IGNORECASE))
                    conf = ConfidenceLevel.HIGH if in_summary else ConfidenceLevel.MEDIUM
                    return TaskNatureClassification(
                        issue_key=issue_key,
                        task_nature=nature,
                        classification_source="summary_keyword" if in_summary else "description_keyword",
                        classification_confidence=conf,
                        matched_rule=f"{rule_name}_{pat}",
                        components=components,
                        labels=labels,
                    )

        # 4. Fallback: UNKNOWN
        return TaskNatureClassification(
            issue_key=issue_key,
            task_nature=TaskNature.UNKNOWN,
            classification_source="fallback",
            classification_confidence=ConfidenceLevel.LOW,
            matched_rule="no_matching_rules",
            components=components,
            labels=labels,
        )
