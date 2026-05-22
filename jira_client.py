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
    page_num = 1

    while True:
        params = {
            "jql": jql,
            "fields": "customfield_10402,summary,status,priority,assignee,created,updated,labels,reporter",
            "maxResults": min(max_results - len(issues), 100),
        }

        if next_page_token:
            params["nextPageToken"] = next_page_token
            log.info(f"  Fetching page {page_num} (nextPageToken: ...{str(next_page_token)[-15:]})")
        else:
            log.info(f"  Fetching page {page_num} (first page)...")

        resp = requests.get(url, params=params, auth=jira_auth(), headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        # print(data)
        batch = data.get("issues", [])
        issues.extend(batch)
        log.info(f"    → Page {page_num} fetched {len(batch)} issues. Total issues so far: {len(issues)}")

        if len(issues) >= max_results:
            log.info(f"Reached user-defined max_results cap ({max_results}).")
            break

        is_last = data.get("isLast")
        next_page_token = data.get("nextPageToken")

        if is_last is True or (next_page_token is None and not batch):
            log.info("Reached the last page (Token/Batch check). Loop break.")
            break

        page_num += 1

    log.info(f"Successfully fetched a total of {len(issues)} issues across {page_num} page(s).")
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


def aggregate_stale_metrics(issues: list[dict]) -> dict[str, int]:
    """Calculate tickets by update age buckets and total 'blocked' count."""
    metrics = {
        "updated_lt_7d": 0,
        "updated_7_30d": 0,
        "updated_gt_30d": 0,
        "total_blocked": 0,
        "wiz_reporter_count": 0
    }
    
    now = datetime.now(timezone.utc)
    
    for issue in issues:
        fields = issue.get("fields", {})
        
        # 💡 Check reporter display name for "Wiz"
        reporter = fields.get("reporter")
        if reporter and reporter.get("displayName") == "Wiz":
            metrics["wiz_reporter_count"] += 1

        # 💡 Blocked check (All issues regardless of age)
        labels = fields.get("labels", [])
        labels_lower = [str(lbl).lower() for lbl in labels]
        is_blocked = "blocked" in labels_lower
        if is_blocked:
            metrics["total_blocked"] += 1

        updated_str = fields.get("updated")
        if not updated_str:
            continue
            
        try:
            dt = datetime.strptime(updated_str, "%Y-%m-%dT%H:%M:%S.%f%z")
        except ValueError:
            try:
                dt = datetime.fromisoformat(updated_str)
            except ValueError:
                continue
                
        age_days = (now - dt).days
        
        # Staleness buckets (only for non-blocked tickets):
        if not is_blocked:
            if age_days < 7:
                metrics["updated_lt_7d"] += 1
            elif age_days < 30:
                metrics["updated_7_30d"] += 1
            else:
                metrics["updated_gt_30d"] += 1
                
    return metrics



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
