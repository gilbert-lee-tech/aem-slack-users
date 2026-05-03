import json
import os
import time

import jwt
import requests
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

load_dotenv()

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
AEM_URL = os.getenv("AEM_URL", "http://localhost:4502").rstrip("/")
AEM_USERNAME = os.getenv("AEM_USERNAME", "admin")
AEM_PASSWORD = os.getenv("AEM_PASSWORD", "admin")
AEM_SERVICE_CREDENTIALS_FILE = os.getenv("AEM_SERVICE_CREDENTIALS_FILE", "./service_credentials.json")

SYSTEM_PRINCIPALS = {"anonymous", "admin"}
SLACK_CACHE_FILE = ".slack_users_cache.json"
SLACK_CACHE_TTL = int(os.getenv("SLACK_CACHE_TTL", "3600"))
AEM_TOKEN_CACHE_FILE = ".aem_token_cache.json"

mcp = FastMCP("aem-slack-users")


# ---------------------------------------------------------------------------
# Shared Slack helpers
# ---------------------------------------------------------------------------

def _get_slack_client() -> WebClient:
    if not SLACK_BOT_TOKEN:
        raise ValueError("SLACK_BOT_TOKEN is not set in .env")
    return WebClient(token=SLACK_BOT_TOKEN)


def _fetch_all_slack_members(client: WebClient) -> list[dict]:
    """Fetch all workspace members via users.list (includes deactivated). Uses disk cache."""
    if os.path.exists(SLACK_CACHE_FILE):
        with open(SLACK_CACHE_FILE, encoding="utf-8") as f:
            cache = json.load(f)
        if time.time() - cache.get("fetched_at", 0) <= SLACK_CACHE_TTL:
            return list(cache["by_email"].values())

    members: list[dict] = []
    by_email: dict[str, dict] = {}
    cursor = None
    while True:
        kwargs = {"limit": 1000}
        if cursor:
            kwargs["cursor"] = cursor
        try:
            resp = client.users_list(**kwargs)
        except SlackApiError as e:
            if e.response.get("error") == "ratelimited":
                time.sleep(int(e.response.headers.get("Retry-After", 60)))
                continue
            raise
        for member in resp["members"]:
            members.append(member)
            if not member.get("is_bot") and member.get("id") != "USLACKBOT":
                email = member.get("profile", {}).get("email", "").strip().lower()
                if email:
                    by_email[email] = member
        cursor = resp.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break

    with open(SLACK_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump({"fetched_at": time.time(), "by_email": by_email}, f)
    return members


def _slack_by_email(client: WebClient) -> dict[str, dict]:
    if os.path.exists(SLACK_CACHE_FILE):
        with open(SLACK_CACHE_FILE, encoding="utf-8") as f:
            cache = json.load(f)
        if time.time() - cache.get("fetched_at", 0) <= SLACK_CACHE_TTL:
            return cache["by_email"]
    _fetch_all_slack_members(client)
    with open(SLACK_CACHE_FILE, encoding="utf-8") as f:
        return json.load(f)["by_email"]


def _format_slack_member(m: dict) -> dict:
    profile = m.get("profile", {})
    if m.get("is_bot") or m.get("id") == "USLACKBOT":
        kind = "bot"
    elif m.get("is_ultra_restricted"):
        kind = "single_channel_guest"
    elif m.get("is_restricted"):
        kind = "multi_channel_guest"
    else:
        kind = "full_member"
    return {
        "slack_user_id": m["id"],
        "name": m.get("real_name") or m.get("name", ""),
        "username": m.get("name", ""),
        "display_name": profile.get("display_name", ""),
        "email": profile.get("email", ""),
        "status": "deactivated" if m.get("deleted") else "active",
        "kind": kind,
        "is_admin": m.get("is_admin", False),
        "is_owner": m.get("is_owner", False),
    }


# ---------------------------------------------------------------------------
# Shared AEM helpers
# ---------------------------------------------------------------------------

def _fetch_aem_users_basic(aem_url: str, username: str, password: str) -> list[dict]:
    params = {
        "path": "/home/users",
        "type": "rep:User",
        "p.limit": "-1",
        "p.hits": "selective",
        "p.properties": "rep:principalName profile/givenName profile/familyName",
    }
    resp = requests.get(
        f"{aem_url}/bin/querybuilder.json",
        params=params,
        auth=(username, password),
        timeout=30,
    )
    resp.raise_for_status()
    return _parse_aem_hits(resp.json().get("hits", []))


def _fetch_aem_users_bearer(aem_url: str, token: str) -> list[dict]:
    params = {
        "path": "/home/users",
        "type": "rep:User",
        "p.limit": "-1",
        "p.hits": "selective",
        "p.properties": "rep:principalName profile/givenName profile/familyName",
    }
    resp = requests.get(
        f"{aem_url}/bin/querybuilder.json",
        params=params,
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
    )
    resp.raise_for_status()
    return _parse_aem_hits(resp.json().get("hits", []))


def _parse_aem_hits(hits: list) -> list[dict]:
    users = []
    for hit in hits:
        principal = hit.get("rep:principalName", "")
        path = hit.get("jcr:path", "")
        if principal in SYSTEM_PRINCIPALS or path.startswith("/home/users/system"):
            continue
        given = hit.get("profile/givenName", "")
        family = hit.get("profile/familyName", "")
        users.append({
            "aem_path": path,
            "aem_username": principal,
            "email": principal,
            "aem_display_name": f"{given} {family}".strip() or principal,
        })
    return users


def _resolve_slack(user: dict, by_email: dict[str, dict]) -> dict:
    slack_user = by_email.get(user["email"].lower())
    if slack_user is None:
        return {**user, "slack_status": "not_found", "slack_user_id": None, "slack_display_name": None}
    return {
        **user,
        "slack_status": "deactivated" if slack_user.get("deleted") else "active",
        "slack_user_id": slack_user["id"],
        "slack_display_name": slack_user.get("profile", {}).get("display_name") or slack_user.get("real_name"),
    }


def _get_aemcs_token() -> str:
    if os.path.exists(AEM_TOKEN_CACHE_FILE):
        with open(AEM_TOKEN_CACHE_FILE, encoding="utf-8") as f:
            cache = json.load(f)
        if time.time() < cache.get("expires_at", 0) - 60:
            return cache["access_token"]

    if not os.path.exists(AEM_SERVICE_CREDENTIALS_FILE):
        raise FileNotFoundError(
            f"Service credentials file not found: {AEM_SERVICE_CREDENTIALS_FILE}. "
            "Download from Cloud Manager → Developer Console → Integrations."
        )
    with open(AEM_SERVICE_CREDENTIALS_FILE, encoding="utf-8") as f:
        creds = json.load(f)

    if "integration" in creds and creds["integration"].get("privateKey"):
        token, expires_in = _token_jwt(creds)
    else:
        token, expires_in = _token_oauth_s2s(creds)

    with open(AEM_TOKEN_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump({"access_token": token, "expires_at": time.time() + expires_in}, f)
    return token


def _token_oauth_s2s(creds: dict) -> tuple[str, int]:
    ims = creds.get("IMS_ENDPOINT", "ims-na1.adobelogin.com")
    resp = requests.post(f"https://{ims}/ims/token/v3", data={
        "grant_type": "client_credentials",
        "client_id": creds["CLIENT_ID"],
        "client_secret": creds["CLIENT_SECRET"],
        "scope": creds.get("SCOPES", "AdobeID,openid,read_organizations"),
    }, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data["access_token"], data.get("expires_in", 86400)


def _token_jwt(creds: dict) -> tuple[str, int]:
    intg = creds["integration"]
    ims = intg.get("imsEndpoint", "ims-na1.adobelogin.com")
    client_id = intg["technicalAccount"]["clientId"]
    payload = {
        "iss": intg["org"],
        "sub": intg["id"],
        "aud": f"https://{ims}/c/{client_id}",
        "exp": int(time.time()) + 300,
        **{f"https://{ims}/s/{s.strip()}": True for s in intg.get("metascopes", "").split(",") if s.strip()},
    }
    signed = jwt.encode(payload, intg["privateKey"], algorithm="RS256")
    resp = requests.post(f"https://{ims}/ims/exchange/jwt", data={
        "client_id": client_id,
        "client_secret": intg["technicalAccount"]["clientSecret"],
        "jwt_token": signed,
    }, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data["access_token"], data.get("expires_in", 86400)


# ---------------------------------------------------------------------------
# MCP tools
# ---------------------------------------------------------------------------

@mcp.tool()
def list_slack_users() -> list[dict]:
    """List all Slack workspace members including bots and deactivated accounts."""
    client = _get_slack_client()
    members = _fetch_all_slack_members(client)
    return [_format_slack_member(m) for m in members]


@mcp.tool()
def list_deactivated_slack_users() -> list[dict]:
    """List only deactivated (deleted) Slack members, excluding bots."""
    client = _get_slack_client()
    members = _fetch_all_slack_members(client)
    return [
        _format_slack_member(m) for m in members
        if m.get("deleted") and not m.get("is_bot") and m.get("id") != "USLACKBOT"
    ]


@mcp.tool()
def check_aem_users() -> list[dict]:
    """
    Cross-reference AEM on-prem users against Slack.
    Uses AEM_URL, AEM_USERNAME, AEM_PASSWORD from .env.
    Returns each AEM user with their Slack status: active, deactivated, or not_found.
    """
    client = _get_slack_client()
    aem_users = _fetch_aem_users_basic(AEM_URL, AEM_USERNAME, AEM_PASSWORD)
    by_email = _slack_by_email(client)
    return [_resolve_slack(u, by_email) for u in aem_users]


@mcp.tool()
def check_aemcs_users() -> list[dict]:
    """
    Cross-reference AEM as a Cloud Service users against Slack.
    Uses AEM_URL and AEM_SERVICE_CREDENTIALS_FILE from .env.
    Auto-detects OAuth Server-to-Server vs legacy JWT credentials.
    Returns each AEM user with their Slack status: active, deactivated, or not_found.
    """
    client = _get_slack_client()
    token = _get_aemcs_token()
    aem_users = _fetch_aem_users_bearer(AEM_URL, token)
    by_email = _slack_by_email(client)
    return [_resolve_slack(u, by_email) for u in aem_users]


if __name__ == "__main__":
    mcp.run()
