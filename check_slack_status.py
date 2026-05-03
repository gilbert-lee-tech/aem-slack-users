import json
import os
import sys
import time
from datetime import datetime

import requests
from dotenv import load_dotenv
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

load_dotenv()

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
API_ENDPOINT_URL = os.getenv("API_ENDPOINT_URL")

# Slack Tier 3 rate limit: ~50 req/min; 1.2s gap keeps us safely under it
RATE_LIMIT_DELAY = 1.2


def fetch_users(url: str) -> list[dict]:
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, list):
        return data
    # Unwrap common envelope shapes: {"users": [...]} or {"data": [...]}
    for key in ("users", "data", "results", "items"):
        if key in data and isinstance(data[key], list):
            return data[key]
    raise ValueError(f"Cannot find a user array in API response. Keys: {list(data.keys())}")


def check_slack_status(client: WebClient, user: dict) -> dict:
    email = user.get("email", "").strip()
    result = {
        "email": email,
        "slack_status": None,
        "slack_user_id": None,
        "is_bot": False,
        "is_guest": False,
        "slack_display_name": None,
    }

    if not email:
        result["slack_status"] = "no_email"
        return result

    try:
        resp = client.users_lookupByEmail(email=email)
        slack_user = resp["user"]
        result["slack_user_id"] = slack_user["id"]
        result["is_bot"] = slack_user.get("is_bot", False)
        result["is_guest"] = slack_user.get("is_restricted", False) or slack_user.get("is_ultra_restricted", False)
        result["slack_display_name"] = slack_user.get("profile", {}).get("display_name") or slack_user.get("real_name")
        result["slack_status"] = "deactivated" if slack_user.get("deleted") else "active"
    except SlackApiError as e:
        error_code = e.response.get("error", "")
        if error_code == "users_not_found":
            result["slack_status"] = "not_found"
        elif error_code == "ratelimited":
            retry_after = int(e.response.headers.get("Retry-After", 60))
            print(f"  Rate limited — waiting {retry_after}s", file=sys.stderr)
            time.sleep(retry_after)
            return check_slack_status(client, user)
        else:
            result["slack_status"] = f"error:{error_code}"

    return result


def main():
    if not SLACK_BOT_TOKEN:
        sys.exit("Error: SLACK_BOT_TOKEN is not set. Copy .env.example to .env and fill it in.")
    if not API_ENDPOINT_URL:
        sys.exit("Error: API_ENDPOINT_URL is not set. Copy .env.example to .env and fill it in.")

    print(f"Fetching users from {API_ENDPOINT_URL} ...")
    users = fetch_users(API_ENDPOINT_URL)
    print(f"Found {len(users)} users to check.")

    client = WebClient(token=SLACK_BOT_TOKEN)
    results = []

    for i, user in enumerate(users, start=1):
        email = user.get("email", "<no email>")
        print(f"[{i}/{len(users)}] Checking {email} ...", end=" ", flush=True)
        result = check_slack_status(client, user)
        # Pass through any extra fields from the source (name, username, etc.)
        for key, value in user.items():
            if key not in result:
                result[key] = value
        results.append(result)
        print(result["slack_status"])
        if i < len(users):
            time.sleep(RATE_LIMIT_DELAY)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = f"slack_status_{timestamp}.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    counts = {}
    for r in results:
        counts[r["slack_status"]] = counts.get(r["slack_status"], 0) + 1

    print(f"\nDone. Results written to {output_file}")
    for status, count in sorted(counts.items()):
        print(f"  {status}: {count}")


if __name__ == "__main__":
    main()
