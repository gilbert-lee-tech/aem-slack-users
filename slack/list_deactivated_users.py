import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from slack_sdk.errors import SlackApiError

from slack.helpers import fetch_all_slack_members, get_slack_client

load_dotenv()


def main():
    if not os.getenv("SLACK_BOT_TOKEN"):
        sys.exit("Error: SLACK_BOT_TOKEN is not set. Copy .env.example to .env and fill it in.")

    client = get_slack_client()

    print("Fetching users from Slack...")
    try:
        raw_members = fetch_all_slack_members(client)
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
