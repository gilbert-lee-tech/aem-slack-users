# aem-slack-users

Cross-reference AEM users against Slack to identify deactivated accounts. Available as standalone Python scripts or as an MCP server for interactive use inside Claude Code.

## Scripts

| Script | Description |
|--------|-------------|
| `list_slack_users.py` | List all Slack workspace members including deactivated |
| `list_deactivated_users.py` | List only deactivated Slack users |
| `check_slack_status.py` | Check a generic API user list against Slack |
| `check_aem_slack_status.py` | Cross-reference AEM on-prem/local users against Slack |
| `check_aemcs_slack_status.py` | Cross-reference AEM as a Cloud Service users against Slack |

## MCP server

`server.py` exposes the same functionality as MCP tools so Claude Code can call them interactively in conversation.

| Tool | Description |
|------|-------------|
| `list_slack_users` | List all Slack workspace members including deactivated |
| `list_deactivated_slack_users` | List only deactivated Slack members |
| `check_aem_users` | Cross-reference AEM on-prem users against Slack |
| `check_aemcs_users` | Cross-reference AEM as a Cloud Service users against Slack |

### Register the MCP server with Claude Code

```bash
claude mcp add aem-slack-users -- /path/to/.venv/bin/python3 /path/to/server.py
```

Once registered, start a new Claude Code session and ask questions like:
> "List all deactivated users in Slack"
> "Cross-reference AEM users against Slack and show me who is deactivated"

## Setup

### 1. Python environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Slack Bot Token

1. Go to https://api.slack.com/apps → **Create New App** → From scratch
2. Under **OAuth & Permissions → Bot Token Scopes**, add:
   - `users:read`
   - `users:read.email`
3. Click **Install to Workspace** → copy the **Bot User OAuth Token** (`xoxb-...`)

### 3. Environment variables

```bash
cp .env.example .env
# Edit .env with your values
```

## Usage

### List all Slack users

```bash
python3 list_slack_users.py
```

Outputs a formatted table and writes `slack_users_<timestamp>.json`.

### List deactivated Slack users

```bash
python3 list_deactivated_users.py
```

Outputs a formatted table and writes `slack_deactivated_<timestamp>.json`.

### Check AEM on-prem users against Slack

Requires a local AEM instance (default: `http://localhost:4502`).

```env
AEM_URL=http://localhost:4502
AEM_USERNAME=admin
AEM_PASSWORD=admin
```

```bash
python3 check_aem_slack_status.py
```

### Check AEM as a Cloud Service users against Slack

1. Download **Service Credentials** from Cloud Manager:
   **Environments → [env] → Developer Console → Integrations → Service Credentials → Download**

2. Set in `.env`:
   ```env
   AEM_URL=https://author-p<PROGRAM_ID>-e<ENV_ID>.adobeaemcloud.com
   AEM_SERVICE_CREDENTIALS_FILE=./service_credentials.json
   ```

3. Run:
   ```bash
   python3 check_aemcs_slack_status.py
   ```

Both AEM scripts look up users by `rep:principalName` (the AEM user ID, which is expected to be an email address) against Slack. They write `aem_slack_report_<timestamp>.json` / `aemcs_slack_report_<timestamp>.json`.

## Output format

Each result record contains:

```json
{
  "aem_path": "/home/users/a/jsmith",
  "aem_username": "jsmith@company.com",
  "email": "jsmith@company.com",
  "aem_display_name": "John Smith",
  "slack_status": "active | deactivated | not_found",
  "slack_user_id": "U01ABC123",
  "slack_display_name": "John Smith"
}
```

## Caching

To avoid hitting Slack API rate limits on large workspaces, Slack user data is cached locally:

| Cache file | TTL | Controls |
|------------|-----|---------|
| `.slack_users_cache.json` | 1 hour | `SLACK_CACHE_TTL=<seconds>` in `.env` |
| `.aem_token_cache.json` | Until token expiry | Delete to force refresh |

To force a fresh Slack fetch:
```bash
rm .slack_users_cache.json
# or
SLACK_CACHE_TTL=0 python3 check_aemcs_slack_status.py
```

## Examples

### Slack

**List all workspace members and their status**
```bash
python3 list_slack_users.py
```
```
STATUS       KIND                   NAME                                EMAIL
------------------------------------------------------------------------------------------
active       full_member            Jane Smith                          jane.smith@company.com
active       full_member            John Doe                            john.doe@company.com
deactivated  full_member            Bob Lee                             bob.lee@company.com
active       bot                    Slackbot
```

**Find all deactivated users**
```bash
python3 list_deactivated_users.py
```
```
Deactivated users: 1

USER ID      NAME                                EMAIL
---------------------------------------------------------------------------
U04XYZ789    Bob Lee                             bob.lee@company.com

Results written to slack_deactivated_20260502_120000.json
```

**Check a specific user's Slack status via the MCP server (Claude Code)**
```
> Is bob.lee@company.com active or deactivated in Slack?
bob.lee@company.com is deactivated in your Slack workspace (user ID: U04XYZ789).
```

---

### AEM

**Cross-reference all AEM on-prem users against Slack**
```bash
python3 check_aem_slack_status.py
```
```
Fetching users from AEM at http://localhost:4502 ...
Found 42 AEM users to check (system/anonymous accounts excluded).
Loading Slack user list (including deactivated) ...
Loaded 198 Slack users into lookup map.
  jane.smith@company.com                   active
  john.doe@company.com                     active
  bob.lee@company.com                      deactivated
  carol.white@company.com                  not_found
  ...

Done. Results written to aem_slack_report_20260502_120000.json
Total AEM users checked: 42
  active: 35
  deactivated: 4
  not_found: 3
```

**Cross-reference AEM as a Cloud Service users against Slack**
```bash
python3 check_aemcs_slack_status.py
```
```
Fetching Adobe IMS access token ...
  Used OAuth Server-to-Server (new format).
Fetching users from AEM at https://author-p12345-e67890.adobeaemcloud.com ...
Found 150 AEM users to check (system/anonymous accounts excluded).
Loading Slack user list (including deactivated) ...
Using cached Slack user list (312s old, TTL=3600s).
Loaded 198 Slack users into lookup map.
  ...

Done. Results written to aemcs_slack_report_20260502_120000.json
```

**Force a fresh Slack user fetch (bypass cache)**
```bash
SLACK_CACHE_TTL=0 python3 check_aemcs_slack_status.py
```

---

## Slack MCP server (optional)

For ad-hoc interactive Slack queries, the official Slack MCP server can also be registered with Claude Code:

```bash
claude mcp add slack \
  -e SLACK_BOT_TOKEN=xoxb-... \
  -e SLACK_TEAM_ID=T... \
  -- npx -y @modelcontextprotocol/server-slack
```

Once registered, start a new Claude Code session and ask questions like:
> "Is john.smith@company.com active or deactivated in Slack?"
