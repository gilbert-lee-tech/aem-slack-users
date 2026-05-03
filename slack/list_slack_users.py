import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from slack_sdk.errors import SlackApiError

from slack.helpers import fetch_all_slack_members, format_slack_member, get_slack_client

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

    users = [format_slack_member(m) for m in raw_members]

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
