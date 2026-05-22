import os
import json
import time
import logging
import requests
import schedule
from datetime import datetime, timezone
from dotenv import load_dotenv

import urllib.parse  

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ─── Configuration ────────────────────────────────────────────────────────────

JIRA_BASE_URL   = os.getenv("JIRA_BASE_URL") 
JIRA_EMAIL      = os.getenv("JIRA_EMAIL")
JIRA_API_TOKEN  = os.getenv("JIRA_API_TOKEN")
SLACK_WEBHOOK   = os.getenv("SLACK_WEBHOOK_URL")
JENKINS_BUILD_URL = os.getenv("BUILD_URL")
GIT_URL         = os.getenv("GIT_URL")
# Define the queues you want to monitor.
# Each entry:  { "name": "Display name", "jql": "JQL query string" }

def jira_auth():
    return (JIRA_EMAIL, JIRA_API_TOKEN)

def fetch_queue_config(project_key: str, queue_id: int) -> dict[str, object]:
    """Fetch the JQL, name, and identifiers for a given Service Desk queue."""
    url = f"{JIRA_BASE_URL}/rest/servicedeskapi/servicedesk/{project_key}/queue/{queue_id}"
    headers = {"Accept": "application/json", "X-ExperimentalApi": "opt-in"}
    resp = requests.get(url, auth=jira_auth(), headers=headers, timeout=15)
    resp.raise_for_status()
    ans = {
        "project_key": project_key,
        "queue_id": queue_id,
        "jql": resp.json()["jql"],
        "name": f"{project_key}: {resp.json()["name"]}"
    }
    return ans

# Wait to fetch configs in the actual run functions instead of globally.

# How often to run (in minutes). Change to suit your schedule.
# RUN_EVERY_MINUTES = 60

# ─── Jira helpers ─────────────────────────────────────────────────────────────


def fetch_issues(jql: str, max_results: int = 1000) -> list[dict]:
    """Return all issues matching a JQL query (handles pagination)."""
    url = f"{JIRA_BASE_URL}/rest/api/3/search/jql"
    headers = {"Accept": "application/json"}
    issues = []
    next_page_token = None

    while True:
        params = {
            "jql": jql,
            "fields": "customfield_10402,summary,status,priority,assignee,created,updated",
            "maxResults": min(max_results - len(issues), 100),
        }

        if next_page_token:
            params["nextPageToken"] = next_page_token

        resp = requests.get(url, params=params, auth=jira_auth(), headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        batch = data.get("issues", [])
        issues.extend(batch)
        # print(data)
        if len(issues) >= max_results:
            log.info(f"Reached user-defined max_results cap ({max_results}).")
            break

        is_last = data.get("isLast")
        next_page_token = data.get("nextPageToken")

        if is_last is True or (next_page_token is None and not batch):
            log.info("Reached the last page (Token/Batch check). Loop break.")
            break

    return issues


def _normalize_sla_cycles(sla_field: object) -> list[dict]:
    if sla_field is None:
        return []
    if isinstance(sla_field, list):
        return sla_field
    if isinstance(sla_field, dict):
        if sla_field.get("ongoingCycle"):
            return [sla_field["ongoingCycle"]]
        return [sla_field]
    return []


def _paused_cycle_has_passed(cycle: dict) -> bool:
    # print(cycle)
    try:
        return cycle['remainingTime']['millis'] <= 0 and cycle['paused'] is True
    except (KeyError, TypeError):
        log.warning("remainingTime missing or malformed in cycle: %s", cycle)
        return False
    # remaining = cycle.get("timeRemaining")
    # if remaining is None:
    #     remaining = cycle.get("remainingTime")
    # if remaining is None:
    #     return False
    # try:
    #     remaining = int(remaining)
    # except (TypeError, ValueError):
    #     return False
    # return remaining < 0


def get_issue_sla_metrics(issue: dict) -> dict[str, int]:
    counts = {"breached": 0, "yellow": 0, "grey": 0}
    sla_cycles = _normalize_sla_cycles(issue["fields"].get("customfield_10402"))

    if any(isinstance(cycle, dict) and cycle.get("breached") is True for cycle in sla_cycles):
        counts["breached"] = 1
    elif any(isinstance(cycle, dict) and cycle.get("paused") is True and _paused_cycle_has_passed(cycle) for cycle in sla_cycles):
        counts["yellow"] = 1
    else:
        counts["grey"] = 1

    return counts


def aggregate_sla_counts(issues: list[dict]) -> dict[str, int]:
    counts = {"breached": 0, "yellow": 0, "grey": 0}
    for issue in issues:
        metrics = get_issue_sla_metrics(issue)
        for key, value in metrics.items():
            counts[key] += value
    return counts


def aggregate_age_buckets(issues: list[dict]) -> dict[str, int]:
    buckets = {
        "< 7 days old": 0,
        "7 to 30 days old": 0,
        "1 to 3 months old": 0,
        "3 to 6 months old": 0,
        "6 months to 1 year": 0,
        "> 1 year": 0
    }
    
    now = datetime.now(timezone.utc)
    
    for issue in issues:
        created_str = issue["fields"].get("created")
        if not created_str:
            continue
            
        try:
            dt = datetime.strptime(created_str, "%Y-%m-%dT%H:%M:%S.%f%z")
        except ValueError:
            try:
                dt = datetime.fromisoformat(created_str)
            except ValueError:
                continue
                
        age_days = (now - dt).days
        
        if age_days < 7:
            buckets["< 7 days old"] += 1
        elif age_days < 30:
            buckets["7 to 30 days old"] += 1
        elif age_days < 90:
            buckets["1 to 3 months old"] += 1
        elif age_days < 180:
            buckets["3 to 6 months old"] += 1
        elif age_days < 365:
            buckets["6 months to 1 year"] += 1
        else:
            buckets["> 1 year"] += 1
            
    return buckets


def group_by_status(issues: list[dict]) -> dict[str, int]:
    """Return { status_name: count } for a list of issues."""
    counts: dict[str, int] = {}
    for issue in issues:
        status = issue["fields"]["status"]["name"]
        counts[status] = counts.get(status, 0) + 1
    return counts


def group_by_assignee(issues: list[dict]) -> dict[str, int]:
    """Return { assignee_name: count } for a list of issues."""
    counts: dict[str, int] = {}
    for issue in issues:
        assignee = issue["fields"].get("assignee")
        if assignee is None:
            name = "Unassigned"
        else:
            name = assignee.get("displayName", assignee.get("name", "Unknown"))
        counts[name] = counts.get(name, 0) + 1
    return counts


def count_unassigned(issues: list[dict]) -> int:
    """Return the number of issues with no assignee."""
    return sum(1 for issue in issues if issue["fields"].get("assignee") is None)

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
    # return # TODO remove this
    resp = requests.post(
        SLACK_WEBHOOK,
        data=json.dumps(payload),
        headers={"Content-Type": "application/json"},
        timeout=10,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Slack returned {resp.status_code}: {resp.text}")

# ─── Main job ──────────────────────────────────────────────────────────────────

def fetch_gts_data() -> dict:
    queue = fetch_queue_config("GTS", 465)
    log.info(f"  Fetching GTS: {queue['name']}")
    issues = fetch_issues(queue["jql"])
    by_status = group_by_status(issues)
    by_assignee = group_by_assignee(issues)
    sla_counts = aggregate_sla_counts(issues)
    
    log.info(
        f"    → {len(issues)} issues across {len(by_status)} statuses, "
        f"{count_unassigned(issues)} unassigned, {sla_counts['breached']} breached, "
        f"{sla_counts['yellow']} yellow, {sla_counts['grey']} grey"
    )
    
    return {
        "name":        queue["name"],
        "project_key": queue["project_key"],
        "queue_id":    queue["queue_id"],
        "jql":         queue["jql"],
        "open_url":    f"{JIRA_BASE_URL}/jira/servicedesk/projects/{queue['project_key']}/queues/custom/{queue['queue_id']}",
        "jenkins_build_url": JENKINS_BUILD_URL,
        "git_url":     GIT_URL,
        "total":       len(issues),
        "unassigned":  count_unassigned(issues),
        "breached":    sla_counts["breached"],
        "yellow":      sla_counts["yellow"],
        "grey":        sla_counts["grey"],
        "by_status":   by_status,
        "by_assignee": by_assignee,
    }


def fetch_noc_data() -> dict:
    queue = fetch_queue_config("NOC", 529)
    log.info(f"  Fetching NOC: {queue['name']}")
    issues = fetch_issues(queue["jql"])
    by_status = group_by_status(issues)
    by_assignee = group_by_assignee(issues)
    age_buckets = aggregate_age_buckets(issues)
    
    log.info(
        f"    → {len(issues)} issues across {len(by_status)} statuses, "
        f"{count_unassigned(issues)} unassigned"
    )
    
    return {
        "name":        queue["name"],
        "project_key": queue["project_key"],
        "queue_id":    queue["queue_id"],
        "jql":         queue["jql"],
        "open_url":    f"{JIRA_BASE_URL}/jira/servicedesk/projects/{queue['project_key']}/queues/custom/{queue['queue_id']}",
        "jenkins_build_url": JENKINS_BUILD_URL,
        "git_url":     GIT_URL,
        "total":       len(issues),
        "unassigned":  count_unassigned(issues),
        "by_status":   by_status,
        "by_assignee": by_assignee,
        "age_buckets": age_buckets,
    }


def run_gts_report():
    log.info("Starting GTS queue report…")
    try:
        summary = fetch_gts_data()
        if summary["total"] == 0:
            log.warning("No GTS data collected — skipping Slack notification.")
            return

        payload = build_gts_slack_payload(summary)
        post_to_slack(payload)
        log.info("GTS Slack notification sent.")
    except Exception as exc:
        log.error(f"  ✗ Failed to run GTS report: {exc}")


def run_noc_report():
    log.info("Starting NOC queue report…")
    try:
        summary = fetch_noc_data()
        if summary["total"] == 0:
            log.warning("No NOC data collected — skipping Slack notification.")
            return

        payload = build_noc_slack_payload(summary)
        post_to_slack(payload)
        log.info("NOC Slack notification sent.")
    except Exception as exc:
        log.error(f"  ✗ Failed to run NOC report: {exc}")


def run_report():
    run_gts_report()
    run_noc_report()


def validate_env():
    missing = [v for v in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN", "SLACK_WEBHOOK_URL", "BUILD_URL")
               if not os.getenv(v)]
    if missing:
        raise EnvironmentError(f"Missing required env vars: {', '.join(missing)}")


validate_env()
run_report()

# print(fetch_queue_jql("GTS", 24))
# schedule.every(RUN_EVERY_MINUTES).minutes.do(run_report)
# log.info(f"Scheduled to run every {RUN_EVERY_MINUTES} minutes. Press Ctrl+C to stop.")
#
# while True:
#     schedule.run_pending()
#     time.sleep(30)
