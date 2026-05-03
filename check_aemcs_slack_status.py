import json
import os
import sys
import time
from datetime import datetime

import jwt
import requests
from dotenv import load_dotenv
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

load_dotenv()

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
AEM_URL = os.getenv("AEM_URL", "").rstrip("/")
AEM_SERVICE_CREDENTIALS_FILE = os.getenv("AEM_SERVICE_CREDENTIALS_FILE", "./service_credentials.json")

SYSTEM_PRINCIPALS = {"anonymous", "admin"}
SLACK_CACHE_FILE = ".slack_users_cache.json"
SLACK_CACHE_TTL = int(os.getenv("SLACK_CACHE_TTL", "3600"))
AEM_TOKEN_CACHE_FILE = ".aem_token_cache.json"


# ---------------------------------------------------------------------------
# Adobe IMS authentication
# ---------------------------------------------------------------------------

def load_credentials(path: str) -> dict:
    if not os.path.exists(path):
        sys.exit(f"Error: Service credentials file not found: {path}\n"
                 "Download it from Cloud Manager → Environments → Developer Console → Integrations.")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _load_token_cache() -> str | None:
    if not os.path.exists(AEM_TOKEN_CACHE_FILE):
        return None
    with open(AEM_TOKEN_CACHE_FILE, encoding="utf-8") as f:
        cache = json.load(f)
    if time.time() < cache.get("expires_at", 0) - 60:  # 60s buffer
        print(f"Using cached AEM access token (expires in {int(cache['expires_at'] - time.time())}s).")
        return cache["access_token"]
    return None


def _save_token_cache(token: str, expires_in: int) -> None:
    with open(AEM_TOKEN_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump({"access_token": token, "expires_at": time.time() + expires_in}, f)


def _get_token_oauth_s2s(creds: dict) -> str:
    """OAuth Server-to-Server (new format). No JWT signing required."""
    ims_endpoint = creds.get("IMS_ENDPOINT", "ims-na1.adobelogin.com")
    url = f"https://{ims_endpoint}/ims/token/v3"
    resp = requests.post(url, data={
        "grant_type": "client_credentials",
        "client_id": creds["CLIENT_ID"],
        "client_secret": creds["CLIENT_SECRET"],
        "scope": creds.get("SCOPES", "AdobeID,openid,read_organizations"),
    }, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data["access_token"], data.get("expires_in", 86400)


def _get_token_jwt(creds: dict) -> tuple[str, int]:
    """Legacy JWT Service Account. Requires PyJWT + cryptography."""
    integration = creds["integration"]
    ims_endpoint = integration.get("imsEndpoint", "ims-na1.adobelogin.com")
    client_id = integration["technicalAccount"]["clientId"]
    client_secret = integration["technicalAccount"]["clientSecret"]
    technical_account_id = integration["id"]
    org_id = integration["org"]
    metascopes = integration.get("metascopes", "")
    private_key = integration["privateKey"]

    payload = {
        "iss": org_id,
        "sub": technical_account_id,
        "aud": f"https://{ims_endpoint}/c/{client_id}",
        "exp": int(time.time()) + 300,
    }
    for scope in metascopes.split(","):
        scope = scope.strip()
        if scope:
            payload[f"https://{ims_endpoint}/s/{scope}"] = True

    signed_jwt = jwt.encode(payload, private_key, algorithm="RS256")

    url = f"https://{ims_endpoint}/ims/exchange/jwt"
    resp = requests.post(url, data={
        "client_id": client_id,
        "client_secret": client_secret,
        "jwt_token": signed_jwt,
    }, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        sys.exit(f"IMS token exchange failed: {data}")
    return data["access_token"], data.get("expires_in", 86400)


def get_access_token(credentials: dict) -> str:
    cached = _load_token_cache()
    if cached:
        return cached

    print("Fetching Adobe IMS access token ...")
    if "integration" in credentials and credentials["integration"].get("privateKey"):
        token, expires_in = _get_token_jwt(credentials)
        print("  Used JWT Service Account (legacy format).")
    else:
        token, expires_in = _get_token_oauth_s2s(credentials)
        print("  Used OAuth Server-to-Server (new format).")

    _save_token_cache(token, expires_in)
    return token


# ---------------------------------------------------------------------------
# AEM user fetch
# ---------------------------------------------------------------------------

def fetch_aem_users(aem_url: str, access_token: str) -> list[dict]:
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
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=60,
    )
    if resp.status_code == 401:
        os.remove(AEM_TOKEN_CACHE_FILE) if os.path.exists(AEM_TOKEN_CACHE_FILE) else None
        sys.exit("Error: AEM returned 401. Token may be expired — delete .aem_token_cache.json and retry.")
    resp.raise_for_status()
    hits = resp.json().get("hits", [])

    users = []
    for hit in hits:
        principal = hit.get("rep:principalName", "")
        path = hit.get("jcr:path", "")
        if principal in SYSTEM_PRINCIPALS or path.startswith("/home/users/system"):
            continue
        given = hit.get("profile/givenName", "")
        family = hit.get("profile/familyName", "")
        display = f"{given} {family}".strip() or principal
        users.append({
            "aem_path": path,
            "aem_username": principal,
            "email": principal,
            "aem_display_name": display,
        })
    return users


# ---------------------------------------------------------------------------
# Slack helpers (shared cache with check_aem_slack_status.py)
# ---------------------------------------------------------------------------

def fetch_slack_users_by_email(client: WebClient) -> dict[str, dict]:
    if os.path.exists(SLACK_CACHE_FILE):
        with open(SLACK_CACHE_FILE, encoding="utf-8") as f:
            cache = json.load(f)
        age = time.time() - cache.get("fetched_at", 0)
        if age <= SLACK_CACHE_TTL:
            print(f"Using cached Slack user list ({int(age)}s old, TTL={SLACK_CACHE_TTL}s).")
            return cache["by_email"]

    by_email: dict[str, dict] = {}
    cursor = None
    page = 0
    while True:
        kwargs = {"limit": 1000}
        if cursor:
            kwargs["cursor"] = cursor
        try:
            resp = client.users_list(**kwargs)
        except SlackApiError as e:
            if e.response.get("error") == "ratelimited":
                wait = int(e.response.headers.get("Retry-After", 60))
                print(f"  Rate limited — waiting {wait}s ...", flush=True)
                time.sleep(wait)
                continue
            raise
        page += 1
        for member in resp["members"]:
            if member.get("is_bot") or member.get("id") == "USLACKBOT":
                continue
            email = member.get("profile", {}).get("email", "").strip().lower()
            if email:
                by_email[email] = member
        print(f"  Page {page}: {len(resp['members'])} members fetched, {len(by_email)} with email so far ...", flush=True)
        cursor = resp.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break

    with open(SLACK_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump({"fetched_at": time.time(), "by_email": by_email}, f)
    return by_email


def resolve_slack_status(user: dict, slack_by_email: dict[str, dict]) -> dict:
    email = user.get("email", "").lower()
    slack_user = slack_by_email.get(email)
    if slack_user is None:
        return {**user, "slack_status": "not_found", "slack_user_id": None, "slack_display_name": None}
    return {
        **user,
        "slack_status": "deactivated" if slack_user.get("deleted") else "active",
        "slack_user_id": slack_user["id"],
        "slack_display_name": (
            slack_user.get("profile", {}).get("display_name") or slack_user.get("real_name")
        ),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if not SLACK_BOT_TOKEN:
        sys.exit("Error: SLACK_BOT_TOKEN is not set.")
    if not AEM_URL:
        sys.exit("Error: AEM_URL is not set (e.g. https://author-p12345-e67890.adobeaemcloud.com).")

    credentials = load_credentials(AEM_SERVICE_CREDENTIALS_FILE)
    access_token = get_access_token(credentials)

    print(f"Fetching users from AEM at {AEM_URL} ...")
    try:
        aem_users = fetch_aem_users(AEM_URL, access_token)
    except requests.exceptions.ConnectionError:
        sys.exit(f"Error: Could not connect to {AEM_URL}.")
    except requests.exceptions.HTTPError as e:
        sys.exit(f"Error: AEM returned {e.response.status_code}.")
    print(f"Found {len(aem_users)} AEM users (system/anonymous excluded).")

    print("Loading Slack user list (including deactivated) ...")
    print(f"  Tip: delete {SLACK_CACHE_FILE} or set SLACK_CACHE_TTL=0 to force a fresh fetch.")
    client = WebClient(token=SLACK_BOT_TOKEN)
    try:
        slack_by_email = fetch_slack_users_by_email(client)
    except SlackApiError as e:
        sys.exit(f"Slack API error: {e.response['error']}")
    print(f"Loaded {len(slack_by_email)} Slack users into lookup map.")

    results = [resolve_slack_status(u, slack_by_email) for u in aem_users]
    for r in results:
        print(f"  {r['aem_username']:40} {r['slack_status']}")

    counts: dict[str, int] = {}
    for r in results:
        counts[r["slack_status"]] = counts.get(r["slack_status"], 0) + 1

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = f"aemcs_slack_report_{timestamp}.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"\nDone. Results written to {output_file}")
    print(f"Total AEM users checked: {len(results)}")
    for status, count in sorted(counts.items()):
        print(f"  {status}: {count}")


if __name__ == "__main__":
    main()
