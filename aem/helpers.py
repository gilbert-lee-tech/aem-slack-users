import requests

SYSTEM_PRINCIPALS = {"anonymous", "admin"}


def fetch_aem_users_basic(aem_url: str, username: str, password: str) -> list[dict]:
    params = {
        "path": "/home/users",
        "type": "rep:User",
        "p.limit": "-1",
        "p.hits": "selective",
        "p.properties": "rep:principalName profile/givenName profile/familyName",
    }
    resp = requests.get(
        f"{aem_url}/bin/querybuilder.json",
        params=params,
        auth=(username, password),
        timeout=30,
    )
    resp.raise_for_status()
    return parse_aem_hits(resp.json().get("hits", []))


def fetch_aem_users_bearer(aem_url: str, token: str) -> list[dict]:
    params = {
        "path": "/home/users",
        "type": "rep:User",
        "p.limit": "-1",
        "p.hits": "selective",
        "p.properties": "rep:principalName profile/givenName profile/familyName",
    }
    resp = requests.get(
        f"{aem_url}/bin/querybuilder.json",
        params=params,
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
    )
    resp.raise_for_status()
    return parse_aem_hits(resp.json().get("hits", []))


def parse_aem_hits(hits: list) -> list[dict]:
    users = []
    for hit in hits:
        principal = hit.get("rep:principalName", "")
        path = hit.get("jcr:path", "")
        if principal in SYSTEM_PRINCIPALS or path.startswith("/home/users/system"):
            continue
        given = hit.get("profile/givenName", "")
        family = hit.get("profile/familyName", "")
        users.append({
            "aem_path": path,
            "aem_username": principal,
            "email": principal,
            "aem_display_name": f"{given} {family}".strip() or principal,
        })
    return users


def resolve_slack(user: dict, by_email: dict) -> dict:
    slack_user = by_email.get(user["email"].lower())
    if slack_user is None:
        return {**user, "slack_status": "not_found", "slack_user_id": None, "slack_display_name": None}
    return {
        **user,
        "slack_status": "deactivated" if slack_user.get("deleted") else "active",
        "slack_user_id": slack_user["id"],
        "slack_display_name": slack_user.get("profile", {}).get("display_name") or slack_user.get("real_name"),
    }
