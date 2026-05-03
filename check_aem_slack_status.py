import json
import os
import sys
import time
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

load_dotenv()

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
AEM_URL = os.getenv("AEM_URL", "http://localhost:4502")
AEM_USERNAME = os.getenv("AEM_USERNAME", "admin")
AEM_PASSWORD = os.getenv("AEM_PASSWORD", "admin")

SYSTEM_PRINCIPALS = {"anonymous", "admin"}
SLACK_CACHE_FILE = ".slack_users_cache.json"
# Re-fetch from Slack after this many seconds (default: 1 hour)
SLACK_CACHE_TTL = int(os.getenv("SLACK_CACHE_TTL", "3600"))


def load_slack_cache() -> dict[str, dict] | None:
    if not os.path.exists(SLACK_CACHE_FILE):
        return None
    with open(SLACK_CACHE_FILE, encoding="utf-8") as f:
        cache = json.load(f)
    age = time.time() - cache.get("fetched_at", 0)
    if age > SLACK_CACHE_TTL:
        return None
    print(f"Using cached Slack user list ({int(age)}s old, TTL={SLACK_CACHE_TTL}s).")
    return cache["by_email"]


def save_slack_cache(by_email: dict[str, dict]) -> None:
    with open(SLACK_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump({"fetched_at": time.time(), "by_email": by_email}, f)


def fetch_slack_users_by_email(client: WebClient) -> dict[str, dict]:
    """Fetch all Slack users (incl. deactivated), keyed by email. Uses disk cache."""
    cached = load_slack_cache()
    if cached is not None:
        return cached

    by_email: dict[str, dict] = {}
    cursor = None
    page = 0
    while True:
        kwargs = {"limit": 1000}  # max page size — minimises API calls
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
        batch = resp["members"]
        for member in batch:
            if member.get("is_bot") or member.get("id") == "USLACKBOT":
                continue
            email = member.get("profile", {}).get("email", "").strip().lower()
            if email:
                by_email[email] = member
        print(f"  Page {page}: {len(batch)} members fetched, {len(by_email)} with email so far ...", flush=True)
        cursor = resp.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break

    save_slack_cache(by_email)
    return by_email


def fetch_aem_users() -> list[dict]:
    params = {
        "path": "/home/users",
        "type": "rep:User",
        "p.limit": "-1",
        "p.hits": "selective",
        "p.properties": "rep:principalName profile/givenName profile/familyName",
    }
    resp = requests.get(
        f"{AEM_URL}/bin/querybuilder.json",
        params=params,
        auth=(AEM_USERNAME, AEM_PASSWORD),
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    hits = data.get("hits", [])

    users = []
    for hit in hits:
        principal = hit.get("rep:principalName", "")
        path = hit.get("jcr:path", "")

        if principal in SYSTEM_PRINCIPALS:
            continue
        if path.startswith("/home/users/system"):
            continue

        given = hit.get("profile/givenName", "")
        family = hit.get("profile/familyName", "")
        display = f"{given} {family}".strip() or principal

        users.append({
            "aem_path": path,
            "aem_username": principal,
            "email": principal,  # AEM user ID is the email used for Slack lookup
            "aem_display_name": display,
        })

    return users


def resolve_slack_status(user: dict, slack_by_email: dict[str, dict]) -> dict:
    email = user.get("email", "").lower()
    slack_user = slack_by_email.get(email)
    if slack_user is None:
        slack_status = "not_found"
        slack_user_id = None
        slack_display_name = None
    else:
        slack_status = "deactivated" if slack_user.get("deleted") else "active"
        slack_user_id = slack_user["id"]
        slack_display_name = (
            slack_user.get("profile", {}).get("display_name") or slack_user.get("real_name")
        )
    return {
        **user,
        "slack_status": slack_status,
        "slack_user_id": slack_user_id,
        "slack_display_name": slack_display_name,
    }


def main():
    for var, name in [(SLACK_BOT_TOKEN, "SLACK_BOT_TOKEN")]:
        if not var:
            sys.exit(f"Error: {name} is not set. Copy .env.example to .env and fill it in.")

    print(f"Fetching users from AEM at {AEM_URL} ...")
    try:
        aem_users = fetch_aem_users()
    except requests.exceptions.ConnectionError:
        sys.exit(f"Error: Could not connect to AEM at {AEM_URL}. Is it running?")
    except requests.exceptions.HTTPError as e:
        sys.exit(f"Error: AEM returned {e.response.status_code}. Check credentials.")

    print(f"Found {len(aem_users)} AEM users to check (system/anonymous accounts excluded).")

    client = WebClient(token=SLACK_BOT_TOKEN)

    print("Loading Slack user list (including deactivated) ...")
    print(f"  Tip: delete {SLACK_CACHE_FILE} or set SLACK_CACHE_TTL=0 to force a fresh fetch.")
    try:
        slack_by_email = fetch_slack_users_by_email(client)
    except SlackApiError as e:
        sys.exit(f"Slack API error: {e.response['error']}")
    print(f"Loaded {len(slack_by_email)} Slack users into lookup map.")

    results = []
    for user in aem_users:
        result = resolve_slack_status(user, slack_by_email)
        results.append(result)
        print(f"  {user['aem_username']:40} {result['slack_status']}")

    counts: dict[str, int] = {}
    for r in results:
        counts[r["slack_status"]] = counts.get(r["slack_status"], 0) + 1

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = f"aem_slack_report_{timestamp}.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"\nDone. Results written to {output_file}")
    print(f"Total AEM users checked: {len(results)}")
    for status, count in sorted(counts.items()):
        print(f"  {status}: {count}")


if __name__ == "__main__":
    main()
