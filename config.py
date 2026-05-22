import os
import logging
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ─── Configuration ────────────────────────────────────────────────────────────

JIRA_BASE_URL     = os.getenv("JIRA_BASE_URL") 
JIRA_EMAIL        = os.getenv("JIRA_EMAIL")
JIRA_API_TOKEN    = os.getenv("JIRA_API_TOKEN")
SLACK_WEBHOOK     = os.getenv("SLACK_WEBHOOK_URL")
JENKINS_BUILD_URL = os.getenv("BUILD_URL")
GIT_URL           = os.getenv("GIT_URL")

def validate_env():
    missing = [v for v in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN", "SLACK_WEBHOOK_URL", "BUILD_URL")
               if not os.getenv(v)]
    if missing:
        raise EnvironmentError(f"Missing required env vars: {', '.join(missing)}")
