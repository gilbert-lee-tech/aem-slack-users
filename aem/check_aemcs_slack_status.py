import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import requests
from dotenv import load_dotenv
from slack_sdk.errors import SlackApiError

from aem.auth import AEM_TOKEN_CACHE_FILE, get_aemcs_token
from aem.helpers import fetch_aem_users_bearer, resolve_slack
from slack.helpers import get_slack_client, slack_by_email

load_dotenv()

AEM_URL = os.getenv("AEM_URL", "").rstrip("/")
AEM_SERVICE_CREDENTIALS_FILE = os.getenv("AEM_SERVICE_CREDENTIALS_FILE", "./service_credentials.json")


def main():
    if not os.getenv("SLACK_BOT_TOKEN"):
        sys.exit("Error: SLACK_BOT_TOKEN is not set.")
    if not AEM_URL:
        sys.exit("Error: AEM_URL is not set (e.g. https://author-p12345-e67890.adobeaemcloud.com).")

    access_token = get_aemcs_token(AEM_SERVICE_CREDENTIALS_FILE)

    print(f"Fetching users from AEM at {AEM_URL} ...")
    try:
        aem_users = fetch_aem_users_bearer(AEM_URL, access_token)
    except requests.exceptions.ConnectionError:
        sys.exit(f"Error: Could not connect to {AEM_URL}.")
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 401:
            if os.path.exists(AEM_TOKEN_CACHE_FILE):
                os.remove(AEM_TOKEN_CACHE_FILE)
            sys.exit("Error: AEM returned 401. Token may be expired — delete .aem_token_cache.json and retry.")
        sys.exit(f"Error: AEM returned {e.response.status_code}.")
    print(f"Found {len(aem_users)} AEM users (system/anonymous excluded).")

    from slack.helpers import SLACK_CACHE_FILE
    client = get_slack_client()
    print("Loading Slack user list (including deactivated) ...")
    print(f"  Tip: delete {SLACK_CACHE_FILE} or set SLACK_CACHE_TTL=0 to force a fresh fetch.")
    try:
        by_email = slack_by_email(client)
    except SlackApiError as e:
        sys.exit(f"Slack API error: {e.response['error']}")
    print(f"Loaded {len(by_email)} Slack users into lookup map.")

    results = [resolve_slack(u, by_email) for u in aem_users]
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
