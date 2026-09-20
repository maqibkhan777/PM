"""Human-readable formatting and report synthesis for PM AI Attention Analysis.

Produces structured, advisory reports that clearly distinguish facts/evidence from AI recommendations.
Purely read-only; does not perform any mutation or execute external actions.
"""

from typing import Any, Dict, List, Optional
from app.services.ai.models import AttentionItemAnalysis, PMAttentionAnalysis


class AIAttentionReportFormatter:
    """Formats PMAttentionAnalysis into structured human-readable text and presentation embeds."""

    @classmethod
    def format_text_report(cls, analysis: PMAttentionAnalysis) -> str:
        """Format a complete PM Attention Analysis into clear, sectioned markdown text."""
        lines: List[str] = [
            f"# 🤖 PM AI Attention Analysis — {analysis.scope_team}",
            f"**Generated:** {analysis.generated_at[:19]} UTC | **Confidence:** {int(analysis.confidence * 100)}%",
            f"**Summary:** {analysis.summary}",
            f"**Human Review Required:** {'Yes' if analysis.requires_human_review else 'No'}",
            "",
            "---",
            f"## 📋 Flagged Attention Items ({len(analysis.attention_items)})",
            "",
        ]

        if not analysis.attention_items:
            lines.append("No active items currently meet the attention threshold.")
            lines.append("")
        else:
            for idx, item in enumerate(analysis.attention_items, start=1):
                lines.extend(cls.format_item_text(item, index=idx))
                lines.append("")

        lines.extend([
            "---",
            "## 💡 Consolidated AI Recommendation",
            f"{analysis.recommendation}",
            "",
            "### Supporting Context & Evidence",
        ])
        for ev in analysis.evidence:
            lines.append(f"- {ev}")

        if analysis.uncertainty_or_missing_info:
            lines.extend([
                "",
                "### ⚠️ Uncertainties & Missing Information",
                f"- {analysis.uncertainty_or_missing_info}",
            ])

        if analysis.proposed_action:
            lines.extend([
                "",
                "### 🛡️ Proposed Action (Advisory Only — Requires Human Approval)",
                f"- **Type:** `{analysis.proposed_action.action_type}`",
                f"- **Target System:** `{analysis.proposed_action.target_system}`",
                f"- **Target ID:** `{analysis.proposed_action.target_id}`",
                f"- **Rationale:** {analysis.proposed_action.rationale or 'N/A'}",
            ])

        return "\n".join(lines)

    @classmethod
    def format_item_text(cls, item: AttentionItemAnalysis, index: int = 1) -> List[str]:
        """Format an individual attention item with strict separation of facts vs AI analysis."""
        lines = [
            f"### {index}. {item.issue_key} — {item.title}",
            "",
            "**Facts:**",
            f"- Status: `{item.current_status}`",
            f"- Assignee: {item.assignee or 'None / Unassigned'}",
            f"- Priority: {item.priority or 'N/A'}",
        ]
        if item.due_date:
            lines.append(f"- Due Date: {item.due_date}")
        if item.inactivity_duration:
            lines.append(f"- Inactivity: {item.inactivity_duration}")
        if item.updated_at:
            lines.append(f"- Last Updated: {item.updated_at}")

        lines.extend([
            "",
            "**AI Analysis:**",
            f"- Attention Reason: {item.attention_reason}",
            f"- Recommendation: {item.recommendation}",
            f"- Confidence: {int(item.confidence * 100)}%",
        ])

        if item.supporting_evidence:
            lines.append("- Evidence:")
            for ev in item.supporting_evidence:
                lines.append(f"  * {ev}")

        if item.uncertainty_or_missing_info:
            lines.append(f"- Missing Information: {item.uncertainty_or_missing_info}")

        if item.proposed_action:
            lines.append(f"- Proposed Action: `{item.proposed_action.action_type}` on `{item.proposed_action.target_id}` (Requires Approval)")

        return lines

    @classmethod
    def format_discord_embeds(cls, analysis: PMAttentionAnalysis) -> Dict[str, Any]:
        """Format PMAttentionAnalysis into compliant Discord embeds with pagination support.
        
        Adheres to Discord limits:
        - Embed title <= 256 chars
        - Description <= 4096 chars
        - Field name <= 256 chars, Field value <= 1024 chars
        - Total characters across all embeds <= 6000
        - Up to 10 embeds per message
        """
        from app.connectors.discord.formatter import COLOR_ATTENTION

        embeds: List[Dict[str, Any]] = []

        # Header embed
        header_desc = (
            f"**Summary:** {analysis.summary}\n"
            f"**Confidence:** {int(analysis.confidence * 100)}% | **Review Required:** {'Yes' if analysis.requires_human_review else 'No'}\n"
            f"**Recommendation:** {analysis.recommendation}\n"
        )
        if analysis.uncertainty_or_missing_info:
            header_desc += f"**Missing Info:** {analysis.uncertainty_or_missing_info}\n"

        header_embed: Dict[str, Any] = {
            "title": f"🤖 PM AI Attention Analysis — {analysis.scope_team}"[:256],
            "description": header_desc[:4096],
            "color": COLOR_ATTENTION,
            "footer": {"text": "Advisory Only — Generated by PM AI"},
            "timestamp": analysis.generated_at,
            "fields": [],
        }

        if analysis.evidence:
            header_embed["fields"].append({
                "name": "Context Evidence"[:256],
                "value": "\n".join(f"• {e}" for e in analysis.evidence)[:1024],
                "inline": False,
            })

        embeds.append(header_embed)

        # Flagged item fields or separate embeds if large
        current_embed = header_embed
        for idx, item in enumerate(analysis.attention_items, start=1):
            facts = f"Status: `{item.current_status}` | Assignee: {item.assignee or 'None'}"
            if item.due_date:
                facts += f" | Due: {item.due_date}"
            if item.inactivity_duration:
                facts += f" | Inactivity: {item.inactivity_duration}"

            field_val = (
                f"**Facts:** {facts}\n"
                f"**AI Reason:** {item.attention_reason}\n"
                f"**Rec:** {item.recommendation} ({int(item.confidence * 100)}%)"
            )
            if item.uncertainty_or_missing_info:
                field_val += f"\n**Missing:** {item.uncertainty_or_missing_info}"
            if item.proposed_action:
                field_val += f"\n**Proposed Action:** `{item.proposed_action.action_type}` (Requires Approval)"

            # If current embed has 25 fields or exceeds limit, start new embed (up to 10 embeds)
            if len(current_embed["fields"]) >= 25 and len(embeds) < 10:
                current_embed = {
                    "title": f"📋 Attention Items (Continued — Page {len(embeds) + 1})"[:256],
                    "color": COLOR_ATTENTION,
                    "fields": [],
                    "footer": {"text": "Advisory Only — Generated by PM AI"},
                }
                embeds.append(current_embed)

            if len(current_embed["fields"]) < 25:
                current_embed["fields"].append({
                    "name": f"{idx}. {item.issue_key} — {item.title}"[:256],
                    "value": field_val[:1024],
                    "inline": False,
                })

        return {"embeds": embeds}

