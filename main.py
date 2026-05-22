import schedule
import time
from config import log, validate_env, JIRA_BASE_URL, JENKINS_BUILD_URL, GIT_URL
from jira_client import (
    fetch_queue_config, fetch_issues, group_by_status, 
    group_by_assignee, aggregate_sla_counts, aggregate_age_buckets, 
    aggregate_stale_metrics, count_unassigned
)
from slack_bot import build_gts_slack_payload, build_noc_slack_payload, post_to_slack

# ─── Main job ──────────────────────────────────────────────────────────────────

def fetch_gts_data() -> dict:
    queue = fetch_queue_config("GTS", 465)
    log.info(f"  Fetching GTS: {queue['name']}")
    issues = fetch_issues(queue["jql"])
    by_status = group_by_status(issues)
    by_assignee = group_by_assignee(issues)
    sla_counts = aggregate_sla_counts(issues)
    stale_metrics = aggregate_stale_metrics(issues)
    
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
        "stale_metrics": stale_metrics,
    }


def fetch_noc_data() -> dict:
    queue = fetch_queue_config("NOC", 529)
    log.info(f"  Fetching NOC: {queue['name']}")
    issues = fetch_issues(queue["jql"])
    by_status = group_by_status(issues)
    by_assignee = group_by_assignee(issues)
    age_buckets = aggregate_age_buckets(issues)
    stale_metrics = aggregate_stale_metrics(issues)
    
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
        "stale_metrics": stale_metrics,
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


if __name__ == "__main__":
    validate_env()
    run_report()

    # schedule.every(60).minutes.do(run_report)
    # log.info("Scheduled to run every 60 minutes. Press Ctrl+C to stop.")
    #
    # while True:
    #     schedule.run_pending()
    #     time.sleep(30)
