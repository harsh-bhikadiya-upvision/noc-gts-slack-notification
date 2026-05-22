import json
import requests
from datetime import datetime
from config import SLACK_WEBHOOK, JENKINS_BUILD_URL, GIT_URL

def build_gts_slack_payload(q: dict) -> dict:
    """Build a dynamic, highly scannable, and fabulous Slack Block Kit message for GTS."""
    now = datetime.now().strftime("%d %b %Y, %H:%M")

    # Header section with a clean layout and dynamic timestamp
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "📊 GTS Queue Status Report",
                "emoji": True,
            },
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"🕒 Generated on *{now}*"}],
        },
        {"type": "divider"},
    ]

    total = q["total"]

    # 1. Format the status breakdown into a clean bulleted list with bolded numbers
    status_lines = "\n".join(
        f"• *{s}*: *`{c}`*"
        for s, c in sorted(q["by_status"].items(), key=lambda x: -x[1])
    )
    if not status_lines:
        status_lines = "• _No tickets found_"

    # 2. Simplified queue header block with bolded numbers
    queue_header_block = {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": (
                f"📂 *{q['name'].upper()}*\n"
                f"📥 *Total Pending:* *`{total}`* ticket{'s' if total != 1 else ''}"
            ),
        },
    }
    blocks.append(queue_header_block)

    # 3. Add the SLA Metrics and Status breakdown side-by-side with bolded metrics
    fields_block = {
        "type": "section",
        "fields": [
            {
                "type": "mrkdwn",
                "text": (
                    f"*Time to Resolution:*\n"
                    f"🔴 Breached: *`{q['breached']}`*\n"
                    f"🟡 Paused but Time Over: *`{q['yellow']}`*\n"
                    f"⚪ Not Breached: *`{q['grey']}`*"
                ),
            },
            {"type": "mrkdwn", "text": f"*Status Breakdown:*\n{status_lines}"},
        ],
    }
    blocks.append(fields_block)
    
    # 3b. Add Assignee breakdown
    assignee_counts = dict(q.get("by_assignee", {}))
    unassigned = assignee_counts.pop("Unassigned", 0)
    
    assignee_items = []
    assignee_items.append(f"• Unassigned: *`{unassigned}`*")
        
    assignee_items.extend(
        f"• {a}: *`{c}`*"
        for a, c in sorted(assignee_counts.items(), key=lambda x: -x[1])
    )
    
    assignee_lines = "\n".join(assignee_items)

    assignee_block = {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": f"*Assignee Breakdown:*\n{assignee_lines}"
        }
    }
    blocks.append(assignee_block)

    # 4. Action block placed cleanly below the metrics row
    action_block = {
        "type": "actions",
        "elements": [
            {
                "type": "button",
                "text": {
                    "type": "plain_text",
                    "text": "🚀 Open Queue",
                    "emoji": True,
                },
                "url": q["open_url"],
                "action_id": f"open_jira_gts",
                "style": "primary"
            }
        ]
    }
    blocks.append(action_block)

    # Divider
    blocks.append({"type": "divider"})

    # Add global actions
    global_actions = {
        "type": "actions",
        "elements": [
            {
                "type": "button",
                "text": {
                    "type": "plain_text",
                    "text": "⚙️ View Jenkins Build",
                    "emoji": True,
                },
                "url": JENKINS_BUILD_URL,
                "action_id": "view_jenkins_build"
            },
            {
                "type": "button",
                "text": {
                    "type": "plain_text",
                    "text": "📦 View Python Script",
                    "emoji": True,
                },
                "url": GIT_URL if GIT_URL else "",
                "action_id": "view_github_script"
            }
        ]
    }
    blocks.append(global_actions)

    return {"blocks": blocks}


def build_noc_slack_payload(q: dict) -> dict:
    """Build a dynamic, highly scannable, and fabulous Slack Block Kit message for NOC."""
    now = datetime.now().strftime("%d %b %Y, %H:%M")

    # Header section with a clean layout and dynamic timestamp
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "📊 NOC Queue Status Report",
                "emoji": True,
            },
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"🕒 Generated on *{now}*"}],
        },
        {"type": "divider"},
    ]

    total = q["total"]

    # 1. Format the status breakdown into a clean bulleted list with bolded numbers
    status_lines = "\n".join(
        f"• *{s}*: *`{c}`*"
        for s, c in sorted(q["by_status"].items(), key=lambda x: -x[1])
    )
    if not status_lines:
        status_lines = "• _No tickets found_"

    # 2. Simplified queue header block with bolded numbers
    queue_header_block = {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": (
                f"📂 *{q['name'].upper()}*\n"
                f"📥 *Total Pending:* *`{total}`* ticket{'s' if total != 1 else ''}"
            ),
        },
    }
    blocks.append(queue_header_block)

    # 3. Add the Ticket Age and Status breakdown side-by-side with bolded metrics
    buckets = q.get("age_buckets", {})
    age_lines = "\n".join(
        f"• {k}: *`{v}`*" for k, v in buckets.items() #if v > 0  # Uncomment to show only non-zero
    )

    fields_block = {
        "type": "section",
        "fields": [
            {
                "type": "mrkdwn",
                "text": (
                    f"*Ticket Age:*\n{age_lines}"
                ),
            },
            {"type": "mrkdwn", "text": f"*Status Breakdown:*\n{status_lines}"},
        ],
    }
    blocks.append(fields_block)

    # 3b. Add Assignee breakdown
    assignee_counts = dict(q.get("by_assignee", {}))
    unassigned = assignee_counts.pop("Unassigned", 0)
    
    assignee_items = []
    assignee_items.append(f"• Unassigned: *`{unassigned}`*")
        
    assignee_items.extend(
        f"• {a}: *`{c}`*"
        for a, c in sorted(assignee_counts.items(), key=lambda x: -x[1])
    )
    
    assignee_lines = "\n".join(assignee_items)

    assignee_block = {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": f"*Assignee Breakdown:*\n{assignee_lines}"
        }
    }
    blocks.append(assignee_block)

    # 4. Action block placed cleanly below the metrics row
    action_block = {
        "type": "actions",
        "elements": [
            {
                "type": "button",
                "text": {
                    "type": "plain_text",
                    "text": "🚀 Open Queue",
                    "emoji": True,
                },
                "url": q["open_url"],
                "action_id": f"open_jira_noc",
                "style": "primary"
            }
        ]
    }
    blocks.append(action_block)

    # Divider
    blocks.append({"type": "divider"})

    # Add global actions
    global_actions = {
        "type": "actions",
        "elements": [
            {
                "type": "button",
                "text": {
                    "type": "plain_text",
                    "text": "⚙️ View Jenkins Build",
                    "emoji": True,
                },
                "url": JENKINS_BUILD_URL,
                "action_id": "view_jenkins_build"
            },
            {
                "type": "button",
                "text": {
                    "type": "plain_text",
                    "text": "📦 View Python Script",
                    "emoji": True,
                },
                "url": GIT_URL if GIT_URL else "",
                "action_id": "view_github_script"
            }
        ]
    }
    blocks.append(global_actions)

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
