import json
import os
import time

from dotenv import load_dotenv
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

load_dotenv()

SLACK_CACHE_FILE = ".slack_users_cache.json"
SLACK_CACHE_TTL = int(os.getenv("SLACK_CACHE_TTL", "3600"))


def get_slack_client() -> WebClient:
    token = os.getenv("SLACK_BOT_TOKEN")
    if not token:
        raise ValueError("SLACK_BOT_TOKEN is not set in .env")
    return WebClient(token=token)


def _load_cache() -> dict | None:
    if not os.path.exists(SLACK_CACHE_FILE):
        return None
    with open(SLACK_CACHE_FILE, encoding="utf-8") as f:
        cache = json.load(f)
    if time.time() - cache.get("fetched_at", 0) <= SLACK_CACHE_TTL:
        return cache
    return None


def _fetch_and_cache(client: WebClient) -> dict:
    members: list[dict] = []
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
        batch = resp["members"]
        members.extend(batch)
        for member in batch:
            if not member.get("is_bot") and member.get("id") != "USLACKBOT":
                email = member.get("profile", {}).get("email", "").strip().lower()
                if email:
                    by_email[email] = member
        print(f"  Page {page}: {len(batch)} members fetched, {len(by_email)} with email so far ...", flush=True)
        cursor = resp.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break

    cache = {"fetched_at": time.time(), "members": members, "by_email": by_email}
    with open(SLACK_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f)
    return cache


def fetch_all_slack_members(client: WebClient) -> list[dict]:
    """Fetch all workspace members via users.list (includes deactivated). Uses disk cache."""
    cache = _load_cache()
    if cache and "members" in cache:
        return cache["members"]
    return _fetch_and_cache(client)["members"]


def slack_by_email(client: WebClient) -> dict[str, dict]:
    """Return non-bot Slack members keyed by lowercase email. Uses disk cache."""
    cache = _load_cache()
    if cache:
        age = int(time.time() - cache.get("fetched_at", 0))
        print(f"Using cached Slack user list ({age}s old, TTL={SLACK_CACHE_TTL}s).")
        return cache["by_email"]
    return _fetch_and_cache(client)["by_email"]


def format_slack_member(m: dict) -> dict:
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
