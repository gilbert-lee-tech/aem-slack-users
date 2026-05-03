import json
import os
import sys
from datetime import datetime

from dotenv import load_dotenv
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

load_dotenv()

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")


def fetch_all_users(client: WebClient) -> list[dict]:
    members = []
    cursor = None
    while True:
        kwargs = {"limit": 200}
        if cursor:
            kwargs["cursor"] = cursor
        resp = client.users_list(**kwargs)
        members.extend(resp["members"])
        cursor = resp.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
    return members


def format_user(member: dict) -> dict:
    profile = member.get("profile", {})
    if member.get("is_bot") or member.get("id") == "USLACKBOT":
        kind = "bot"
    elif member.get("is_ultra_restricted"):
        kind = "single_channel_guest"
    elif member.get("is_restricted"):
        kind = "multi_channel_guest"
    else:
        kind = "full_member"

    return {
        "slack_user_id": member["id"],
        "name": member.get("real_name") or member.get("name", ""),
        "username": member.get("name", ""),
        "display_name": profile.get("display_name", ""),
        "email": profile.get("email", ""),
        "status": "deactivated" if member.get("deleted") else "active",
        "kind": kind,
        "is_admin": member.get("is_admin", False),
        "is_owner": member.get("is_owner", False),
    }


def main():
    if not SLACK_BOT_TOKEN:
        sys.exit("Error: SLACK_BOT_TOKEN is not set. Copy .env.example to .env and fill it in.")

    client = WebClient(token=SLACK_BOT_TOKEN)

    print("Fetching users from Slack...")
    try:
        raw_members = fetch_all_users(client)
    except SlackApiError as e:
        sys.exit(f"Slack API error: {e.response['error']}")

    users = [format_user(m) for m in raw_members]

    # Summary counts
    counts = {}
    for u in users:
        key = f"{u['status']}:{u['kind']}"
        counts[key] = counts.get(key, 0) + 1

    print(f"\nTotal: {len(users)} accounts\n")
    col = "{:<12} {:<22} {:<35} {}"
    print(col.format("STATUS", "KIND", "NAME", "EMAIL"))
    print("-" * 90)
    for u in sorted(users, key=lambda x: (x["status"], x["kind"], x["name"].lower())):
        print(col.format(u["status"], u["kind"], u["name"][:34], u["email"]))

    print("\nSummary:")
    for key, count in sorted(counts.items()):
        print(f"  {key}: {count}")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = f"slack_users_{timestamp}.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(users, f, indent=2, ensure_ascii=False)
    print(f"\nFull list written to {output_file}")


if __name__ == "__main__":
    main()
