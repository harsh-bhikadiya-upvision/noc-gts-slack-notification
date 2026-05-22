import json
import requests
from datetime import datetime
from config import SLACK_WEBHOOK, JENKINS_BUILD_URL, GIT_URL

def _build_base_slack_payload(q: dict, queue_type: str, metric_field: dict) -> dict:
    """Core helper function to generate scannable and beautiful Slack payloads."""
    now = datetime.now().strftime("%d %b %Y, %H:%M")
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
        f"• Updated < 7 Days: *`{stale.get('updated_recently', 0)}`*\n"
        f"• Idle > 7 Days: *`{stale.get('stale_not_blocked', 0)}`*\n"
        f"• Blocked (Excluded): *`{stale.get('stale_blocked', 0)}`*"
    )

    # 3. Format Assignee Breakdown
    assignee_counts = dict(q.get("by_assignee", {}))
    unassigned = assignee_counts.pop("Unassigned", 0)
    assignee_items = [f"• Unassigned: *`{unassigned}`*"] + [
        f"• {name}: *`{count}`*"
        for name, count in sorted(assignee_counts.items(), key=lambda x: -x[1])
    ]
    assignee_lines = "\n".join(assignee_items)

    # Construct Block Kit Structure
    blocks = [
        # Header & Context
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"📊 {queue_type.upper()} Queue Status Report",
                "emoji": True,
            },
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"🕒 Generated on *{now}*"}],
        },
        {"type": "divider"},
        
        # Queue Title & Summary
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"📂 *{q['name'].upper()}*\n"
                    f"📥 *Total Pending:* *`{total}`* ticket{'s' if total != 1 else ''}"
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
        
        # Grid Row 2 (Status Breakdown + Assignee Breakdown) - Spacers completely removed
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Status Breakdown:*\n{status_lines}"},
                {"type": "mrkdwn", "text": f"*Assignee Breakdown:*\n{assignee_lines}"},
            ],
        },
        
        # Queue-Specific Call to Action
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "🚀 Open Queue", "emoji": True},
                    "url": q["open_url"],
                    "action_id": f"open_jira_{queue_type.lower()}",
                    "style": "primary"
                }
            ]
        },
        {"type": "divider"},
        
        # Global Action Links
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "⚙️ View Jenkins Build", "emoji": True},
                    "url": JENKINS_BUILD_URL,
                    "action_id": "view_jenkins_build"
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "📦 View Python Script", "emoji": True},
                    "url": GIT_URL or "",
                    "action_id": "view_github_script"
                }
            ]
        }
    ]

    return {"blocks": blocks}


def build_gts_slack_payload(q: dict) -> dict:
    """Build a dynamic, highly scannable, and fabulous Slack Block Kit message for GTS."""
    gts_metric = {
        "type": "mrkdwn",
        "text": (
            f"*Time to Resolution:*\n"
            f"🔴 Breached: *`{q.get('breached', 0)}`*\n"
            f"🟡 Paused but Time Over: *`{q.get('yellow', 0)}`*\n"
            f"⚪ Not Breached: *`{q.get('grey', 0)}`*"
        ),
    }
    return _build_base_slack_payload(q, queue_type="gts", metric_field=gts_metric)


def build_noc_slack_payload(q: dict) -> dict:
    """Build a dynamic, highly scannable, and fabulous Slack Block Kit message for NOC."""
    buckets = q.get("age_buckets", {})
    age_lines = "\n".join(f"• {k}: *`{v}`*" for k, v in buckets.items())
    
    noc_metric = {
        "type": "mrkdwn",
        "text": f"*Ticket Age:*\n{age_lines or '• _No data_'}",
    }
    return _build_base_slack_payload(q, queue_type="noc", metric_field=noc_metric)

def post_to_slack(payload: dict) -> None:
    resp = requests.post(
        SLACK_WEBHOOK,
        data=json.dumps(payload),
        headers={"Content-Type": "application/json"},
        timeout=10,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Slack returned {resp.status_code}: {resp.text}")
