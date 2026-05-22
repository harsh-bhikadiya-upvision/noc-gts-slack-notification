import requests
from datetime import datetime, timezone
import logging
from config import JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN, log

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
    try:
        return cycle['remainingTime']['millis'] <= 0 and cycle['paused'] is True
    except (KeyError, TypeError):
        log.warning("remainingTime missing or malformed in cycle: %s", cycle)
        return False


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
