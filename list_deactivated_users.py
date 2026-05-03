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


def main():
    if not SLACK_BOT_TOKEN:
        sys.exit("Error: SLACK_BOT_TOKEN is not set. Copy .env.example to .env and fill it in.")

    client = WebClient(token=SLACK_BOT_TOKEN)

    print("Fetching users from Slack...")
    try:
        raw_members = fetch_all_users(client)
    except SlackApiError as e:
        sys.exit(f"Slack API error: {e.response['error']}")

    deactivated = [
        m for m in raw_members
        if m.get("deleted") and not m.get("is_bot") and m.get("id") != "USLACKBOT"
    ]

    if not deactivated:
        print("No deactivated users found.")
        return

    results = []
    for m in deactivated:
        profile = m.get("profile", {})
        results.append({
            "slack_user_id": m["id"],
            "name": m.get("real_name") or m.get("name", ""),
            "username": m.get("name", ""),
            "display_name": profile.get("display_name", ""),
            "email": profile.get("email", ""),
            "status": "deactivated",
        })

    results.sort(key=lambda x: x["name"].lower())

    print(f"\nDeactivated users: {len(results)}\n")
    col = "{:<12} {:<35} {}"
    print(col.format("USER ID", "NAME", "EMAIL"))
    print("-" * 75)
    for u in results:
        print(col.format(u["slack_user_id"], u["name"][:34], u["email"]))

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = f"slack_deactivated_{timestamp}.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\nResults written to {output_file}")


if __name__ == "__main__":
    main()
