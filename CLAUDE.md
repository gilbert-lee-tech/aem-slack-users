# CLAUDE.md

## Project overview

Python scripts that cross-reference AEM users against Slack to identify deactivated accounts. Users are looked up by AEM `rep:principalName` (expected to be an email address) against the full Slack user list including deactivated users.

## Running scripts

Always use the project venv:

```bash
.venv/bin/python3 <script>.py
```

To set up the venv from scratch:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Scripts

Scripts are organised into `slack/` and `aem/` packages mirroring the MCP server tool groups.

| Script | Purpose |
|--------|---------|
| `slack/list_slack_users.py` | List all Slack members (active + deactivated) |
| `slack/list_deactivated_users.py` | List only deactivated Slack members |
| `aem/check_aem_slack_status.py` | AEM on-prem (Basic Auth) → Slack cross-reference |
| `aem/check_aemcs_slack_status.py` | AEM as a Cloud Service (Adobe IMS) → Slack cross-reference |
| `aem/check_slack_status.py` | Generic API endpoint → Slack cross-reference |

Shared helpers live in `slack/helpers.py`, `aem/helpers.py`, and `aem/auth.py`.

## Key design decisions

- **Slack user lookup uses `users.list` not `users_lookupByEmail`** — `lookupByEmail` silently skips deactivated users; `users.list` returns everyone including deactivated accounts.
- **AEM user ID (`rep:principalName`) is used as the email** for Slack lookup, not the AEM profile email field.
- **Slack user list is cached** to `.slack_users_cache.json` (default TTL: 1 hour) to avoid rate limit issues on large workspaces. Delete the file or set `SLACK_CACHE_TTL=0` to force a fresh fetch.
- **AEMaaCS access token is cached** to `.aem_token_cache.json` until expiry. Delete to force re-authentication.
- **System users are excluded**: `anonymous`, `admin`, and any user under `/home/users/system`.

## Environment variables

| Variable | Used by | Description |
|----------|---------|-------------|
| `SLACK_BOT_TOKEN` | all | Slack Bot Token (`xoxb-...`). Scopes: `users:read`, `users:read.email` |
| `SLACK_TEAM_ID` | MCP only | Slack workspace ID (`T...`) |
| `SLACK_CACHE_TTL` | AEM scripts | Seconds before Slack cache expires (default: `3600`) |
| `AEM_URL` | AEM scripts | AEM instance URL |
| `AEM_USERNAME` | `check_aem_slack_status.py` | AEM Basic Auth username (default: `admin`) |
| `AEM_PASSWORD` | `check_aem_slack_status.py` | AEM Basic Auth password (default: `admin`) |
| `AEM_SERVICE_CREDENTIALS_FILE` | `check_aemcs_slack_status.py` | Path to Adobe IMS credentials JSON |

## AEMaaCS authentication

`check_aemcs_slack_status.py` auto-detects the credential format from the JSON file:

- **OAuth Server-to-Server** (new): JSON has `CLIENT_ID`, `CLIENT_SECRET`, `SCOPES` keys
- **JWT Service Account** (legacy): JSON has an `integration.privateKey` field

Download credentials from Cloud Manager → Environments → Developer Console → Integrations → Service Credentials.

## Slack MCP server

Registered locally for this project:

```bash
claude mcp get slack
```

The MCP server (`@modelcontextprotocol/server-slack`) enables interactive Slack queries in Claude Code sessions but does not expose a user listing tool — use `slack/list_slack_users.py` for bulk operations. MCP tools are only available in sessions started after the server was registered.

## Output files

All scripts write timestamped JSON to the project root (gitignored via `.gitignore`):

- `slack_users_<timestamp>.json`
- `slack_deactivated_<timestamp>.json`
- `aem_slack_report_<timestamp>.json`
- `aemcs_slack_report_<timestamp>.json`
