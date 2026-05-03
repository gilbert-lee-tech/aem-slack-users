import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import requests
from dotenv import load_dotenv
from slack_sdk.errors import SlackApiError

from aem.helpers import fetch_aem_users_basic, resolve_slack
from slack.helpers import get_slack_client, slack_by_email

load_dotenv()

AEM_URL = os.getenv("AEM_URL", "http://localhost:4502")
AEM_USERNAME = os.getenv("AEM_USERNAME", "admin")
AEM_PASSWORD = os.getenv("AEM_PASSWORD", "admin")


def main():
    if not os.getenv("SLACK_BOT_TOKEN"):
        sys.exit("Error: SLACK_BOT_TOKEN is not set. Copy .env.example to .env and fill it in.")

    print(f"Fetching users from AEM at {AEM_URL} ...")
    try:
        aem_users = fetch_aem_users_basic(AEM_URL, AEM_USERNAME, AEM_PASSWORD)
    except requests.exceptions.ConnectionError:
        sys.exit(f"Error: Could not connect to AEM at {AEM_URL}. Is it running?")
    except requests.exceptions.HTTPError as e:
        sys.exit(f"Error: AEM returned {e.response.status_code}. Check credentials.")
    print(f"Found {len(aem_users)} AEM users to check (system/anonymous accounts excluded).")

    from slack.helpers import SLACK_CACHE_FILE
    client = get_slack_client()
    print("Loading Slack user list (including deactivated) ...")
    print(f"  Tip: delete {SLACK_CACHE_FILE} or set SLACK_CACHE_TTL=0 to force a fresh fetch.")
    try:
        by_email = slack_by_email(client)
    except SlackApiError as e:
        sys.exit(f"Slack API error: {e.response['error']}")
    print(f"Loaded {len(by_email)} Slack users into lookup map.")

    results = []
    for user in aem_users:
        result = resolve_slack(user, by_email)
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
