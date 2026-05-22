import json
import requests
from datetime import datetime
from config import SLACK_WEBHOOK, JENKINS_BUILD_URL, GIT_URL


def _format_queue_blocks(q: dict, queue_type: str, metric_field: dict) -> list:
    """Helper to generate layout blocks for an individual queue (without inline buttons)."""
    total = q["total"]
    
    # 1. Format Status Breakdown
    status_items = [
        f"• *{status}*: *`{count}`*"
        for status, count in sorted(q["by_status"].items(), key=lambda x: -x[1])
    ]
    status_lines = "\n".join(status_items) if status_items else "• _No tickets found_"

    # 2. Format Stale Metrics Review
    stale = q.get("stale_metrics", {})
    stale_lines = (
        f"• Updated < 7 Days: *`{stale.get('updated_lt_7d', 0)}`*\n"
        f"• Idle 7 - 30 Days: *`{stale.get('updated_7_30d', 0)}`*\n"
        f"• Idle > 30 Days: *`{stale.get('updated_gt_30d', 0)}`*"
    )
    if queue_type.lower() == "noc":
        stale_lines += (
            f"\n• Blocked (Total): *`{stale.get('total_blocked', 0)}`*\n"
            f"• Reporter 'Wiz': *`{stale.get('wiz_reporter_count', 0)}`*"  # 💡 Added right below Blocked
        )

    # 3. Format Assignee Breakdown
    assignee_counts = dict(q.get("by_assignee", {}))
    unassigned = assignee_counts.pop("Unassigned", 0)
    assignee_items = [f"• Unassigned: *`{unassigned}`*"] + [
        f"• {name}: *`{count}`*"
        for name, count in sorted(assignee_counts.items(), key=lambda x: -x[1])
    ]
    assignee_lines = "\n".join(assignee_items)

    # Warning Check: Unassigned, Breached (GTS), or > 30d Stale
    warnings = []
    if q.get("unassigned", 0) > 0:
        warnings.append(f"Unassigned ({q['unassigned']})")
    if queue_type.lower() == "gts" and q.get("breached", 0) > 0:
        warnings.append(f"Breached ({q['breached']})")
    if stale.get("updated_gt_30d", 0) > 0:
        warnings.append(f"Idle > 30d ({stale['updated_gt_30d']})")
    
    warning_line = ""
    if warnings:
        warning_line = f"\n🚨 *Needs Attention:* {', '.join(warnings)}"

    # Dynamic sub-blocks for this individual queue segment (Buttons removed from here)
    blocks = [
        # Queue Title & Summary
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"📂 *{q['name'].upper()}* ({queue_type.upper()})\n"
                    f"📥 *Total Pending:* *`{total}`* ticket{'s' if total != 1 else ''}"
                    f"{warning_line}"
                ),
            },
        },
        # Grid Row 1 (Metric Field + Stale Tickets Review)
        {
            "type": "section",
            "fields": [
                metric_field,
                {"type": "mrkdwn", "text": f"*Stale Tickets Review:*\n{stale_lines}"},
            ],
        },
    ]

    # Add Status/Assignee breakdown row
    blocks.append({
        "type": "section",
        "fields": [
            {"type": "mrkdwn", "text": f"*Status Breakdown:*\n{status_lines}"},
            {"type": "mrkdwn", "text": f"*Assignee Breakdown:*\n{assignee_lines}"},
        ],
    })

    return blocks


def build_combined_slack_payload(gts_q: dict, noc_q: dict) -> dict:
    """Combines GTS and NOC summaries with all action buttons aligned in a single row at the bottom."""
    now = datetime.now().strftime("%d %b %Y, %H:%M")
    
    # Pre-build metric blocks for GTS
    gts_metric = {
        "type": "mrkdwn",
        "text": (
            f"*Time to Resolution:*\n"
            f"🔴 Breached: *`{gts_q.get('breached', 0)}`*\n"
            f"🟡 Paused but Time Over: *`{gts_q.get('yellow', 0)}`*\n"
            f"⚪ Not Breached: *`{gts_q.get('grey', 0)}`*"
        ),
    }
    
    # Pre-build metric blocks for NOC
    buckets = noc_q.get("age_buckets", {})
    age_lines = "\n".join(f"• {k}: *`{v}`*" for k, v in buckets.items())
    noc_metric = {
        "type": "mrkdwn",
        "text": f"*Ticket Age:*\n{age_lines or '• _No data_'}",
    }

    # Generate content segments
    gts_blocks = _format_queue_blocks(gts_q, "gts", gts_metric)
    noc_blocks = _format_queue_blocks(noc_q, "noc", noc_metric)

    # Master payload structure
    blocks = [
        # Main Global Header
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "📊 Operations Queue Status Report",
                "emoji": True,
            },
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"🕒 Generated on *{now}*"}],
        },
        {"type": "divider"},
        
        # GTS Segment
        *gts_blocks,
        
        {"type": "divider"},
        
        # NOC Segment
        *noc_blocks,
        
        {"type": "divider"},
        
        # Consolidated Action Button Row at the very bottom
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "🚀 Open GTS Queue", "emoji": True},
                    "url": gts_q["open_url"],
                    "action_id": "open_jira_gts",
                    "style": "primary"
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "🚀 Open NOC Queue", "emoji": True},
                    "url": noc_q["open_url"],
                    "action_id": "open_jira_noc",
                    "style": "primary"
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "⚙️ View Jenkins", "emoji": True},
                    "url": JENKINS_BUILD_URL,
                    "action_id": "view_jenkins_build"
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "📦 View Script", "emoji": True},
                    "url": GIT_URL or "",
                    "action_id": "view_github_script"
                }
            ]
        }
    ]

    return {"blocks": blocks}

def post_to_slack(payload: dict) -> None:
    resp = requests.post(
        SLACK_WEBHOOK,
        data=json.dumps(payload),
        headers={"Content-Type": "application/json"},
        timeout=10,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Slack returned {resp.status_code}: {resp.text}")
