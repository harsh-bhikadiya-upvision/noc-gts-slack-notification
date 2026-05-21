import os
import json
import time
import logging
import requests
import schedule
from datetime import datetime
from dotenv import load_dotenv

from datetime import datetime
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

QUEUE_CONFIG = [
    fetch_queue_config("GTS", 24),
    fetch_queue_config("NOC", 25),
]

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


def group_by_status(issues: list[dict]) -> dict[str, int]:
    """Return { status_name: count } for a list of issues."""
    counts: dict[str, int] = {}
    for issue in issues:
        status = issue["fields"]["status"]["name"]
        counts[status] = counts.get(status, 0) + 1
    return counts


def count_unassigned(issues: list[dict]) -> int:
    """Return the number of issues with no assignee."""
    return sum(1 for issue in issues if issue["fields"].get("assignee") is None)

def build_slack_payload(queue_summaries: list[dict], jira_base_url: str) -> dict:
    """Build a dynamic, highly scannable, and fabulous Slack Block Kit message."""
    now = datetime.now().strftime("%d %b %Y, %H:%M")

    # Header section with a clean layout and dynamic timestamp
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "📊 Jira Queue Status Report",
                "emoji": True,
            },
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"🕒 Generated on *{now}*"}],
        },
        {"type": "divider"},
    ]

    for q in queue_summaries:
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
                    f"📥 *Total Pending:* *`{total}`* ticket{'s' if total != 1 else ''}  |  "
                    f"👤 *Unassigned:* *`{q['unassigned']}`*"
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
        queue_slug = q['name'].lower().replace(' ', '_')
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
                    "action_id": f"open_jira_{q['name'].lower().replace(' ', '_')}",
                    "style": "primary"
                },
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "⚙️ View Jenkins Build",
                        "emoji": True,
                    },
                    "url": q['jenkins_build_url'], # Fallback URL if missing
                    "action_id": f"view_jenkins_{queue_slug}"
                }
            ]
        }
        blocks.append(action_block)

        # Divider between multiple queues
        blocks.append({"type": "divider"})

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

# ─── Main job ──────────────────────────────────────────────────────────────────

def run_report():
    log.info("Starting Jira queue report…")
    queue_summaries = []

    for queue in QUEUE_CONFIG:
        log.info(f"  Fetching: {queue['name']}")
        try:
            issues = fetch_issues(queue["jql"])
            by_status = group_by_status(issues)
            sla_counts = aggregate_sla_counts(issues)
            queue_summaries.append({
                "name":        queue["name"],
                "project_key": queue["project_key"],
                "queue_id":    queue["queue_id"],
                "jql":         queue["jql"],
                "open_url":    f"{JIRA_BASE_URL}/jira/servicedesk/projects/{queue['project_key']}/queues/custom/{queue['queue_id']}",
                "jenkins_build_url": JENKINS_BUILD_URL,
                "total":       len(issues),
                "unassigned":  count_unassigned(issues),
                "breached":    sla_counts["breached"],
                "yellow":      sla_counts["yellow"],
                "grey":        sla_counts["grey"],
                "by_status":   by_status,
            })
            log.info(
                f"    → {len(issues)} issues across {len(by_status)} statuses, "
                f"{count_unassigned(issues)} unassigned, {sla_counts['breached']} breached, "
                f"{sla_counts['yellow']} yellow, {sla_counts['grey']} grey"
            )
        except Exception as exc:
            log.error(f"    ✗ Failed to fetch '{queue['name']}': {exc}")

    if not queue_summaries:
        log.warning("No data collected — skipping Slack notification.")
        return

    payload = build_slack_payload(queue_summaries, JIRA_BASE_URL)
    post_to_slack(payload)
    log.info("Slack notification sent.")


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
