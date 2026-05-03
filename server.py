import os

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from aem.auth import get_aemcs_token
from aem.helpers import fetch_aem_users_basic, fetch_aem_users_bearer, resolve_slack
from slack.helpers import fetch_all_slack_members, format_slack_member, get_slack_client, slack_by_email

load_dotenv()

AEM_URL = os.getenv("AEM_URL", "http://localhost:4502").rstrip("/")
AEM_USERNAME = os.getenv("AEM_USERNAME", "admin")
AEM_PASSWORD = os.getenv("AEM_PASSWORD", "admin")
AEM_SERVICE_CREDENTIALS_FILE = os.getenv("AEM_SERVICE_CREDENTIALS_FILE", "./service_credentials.json")

mcp = FastMCP("aem-slack-users")


@mcp.tool()
def list_slack_users() -> list[dict]:
    """List all Slack workspace members including bots and deactivated accounts."""
    client = get_slack_client()
    members = fetch_all_slack_members(client)
    return [format_slack_member(m) for m in members]


@mcp.tool()
def list_deactivated_slack_users() -> list[dict]:
    """List only deactivated (deleted) Slack members, excluding bots."""
    client = get_slack_client()
    members = fetch_all_slack_members(client)
    return [
        format_slack_member(m) for m in members
        if m.get("deleted") and not m.get("is_bot") and m.get("id") != "USLACKBOT"
    ]


@mcp.tool()
def check_aem_users() -> list[dict]:
    """
    Cross-reference AEM on-prem users against Slack.
    Uses AEM_URL, AEM_USERNAME, AEM_PASSWORD from .env.
    Returns each AEM user with their Slack status: active, deactivated, or not_found.
    """
    client = get_slack_client()
    aem_users = fetch_aem_users_basic(AEM_URL, AEM_USERNAME, AEM_PASSWORD)
    by_email = slack_by_email(client)
    return [resolve_slack(u, by_email) for u in aem_users]


@mcp.tool()
def check_aemcs_users() -> list[dict]:
    """
    Cross-reference AEM as a Cloud Service users against Slack.
    Uses AEM_URL and AEM_SERVICE_CREDENTIALS_FILE from .env.
    Auto-detects OAuth Server-to-Server vs legacy JWT credentials.
    Returns each AEM user with their Slack status: active, deactivated, or not_found.
    """
    client = get_slack_client()
    token = get_aemcs_token(AEM_SERVICE_CREDENTIALS_FILE)
    aem_users = fetch_aem_users_bearer(AEM_URL, token)
    by_email = slack_by_email(client)
    return [resolve_slack(u, by_email) for u in aem_users]


if __name__ == "__main__":
    mcp.run()
