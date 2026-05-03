import json
import os
import sys
import time

import jwt
import requests

AEM_TOKEN_CACHE_FILE = ".aem_token_cache.json"


def get_aemcs_token(credentials_file: str) -> str:
    """Get an AEM as a Cloud Service access token. Uses disk cache until expiry."""
    if os.path.exists(AEM_TOKEN_CACHE_FILE):
        with open(AEM_TOKEN_CACHE_FILE, encoding="utf-8") as f:
            cache = json.load(f)
        if time.time() < cache.get("expires_at", 0) - 60:
            print(f"Using cached AEM access token (expires in {int(cache['expires_at'] - time.time())}s).")
            return cache["access_token"]

    if not os.path.exists(credentials_file):
        sys.exit(
            f"Error: Service credentials file not found: {credentials_file}\n"
            "Download from Cloud Manager → Environments → Developer Console → Integrations."
        )
    with open(credentials_file, encoding="utf-8") as f:
        creds = json.load(f)

    print("Fetching Adobe IMS access token ...")
    if "integration" in creds and creds["integration"].get("privateKey"):
        token, expires_in = _token_jwt(creds)
        print("  Used JWT Service Account (legacy format).")
    else:
        token, expires_in = _token_oauth_s2s(creds)
        print("  Used OAuth Server-to-Server (new format).")

    with open(AEM_TOKEN_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump({"access_token": token, "expires_at": time.time() + expires_in}, f)
    return token


def _token_oauth_s2s(creds: dict) -> tuple[str, int]:
    ims = creds.get("IMS_ENDPOINT", "ims-na1.adobelogin.com")
    resp = requests.post(f"https://{ims}/ims/token/v3", data={
        "grant_type": "client_credentials",
        "client_id": creds["CLIENT_ID"],
        "client_secret": creds["CLIENT_SECRET"],
        "scope": creds.get("SCOPES", "AdobeID,openid,read_organizations"),
    }, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data["access_token"], data.get("expires_in", 86400)


def _token_jwt(creds: dict) -> tuple[str, int]:
    intg = creds["integration"]
    ims = intg.get("imsEndpoint", "ims-na1.adobelogin.com")
    client_id = intg["technicalAccount"]["clientId"]
    payload = {
        "iss": intg["org"],
        "sub": intg["id"],
        "aud": f"https://{ims}/c/{client_id}",
        "exp": int(time.time()) + 300,
        **{f"https://{ims}/s/{s.strip()}": True for s in intg.get("metascopes", "").split(",") if s.strip()},
    }
    signed = jwt.encode(payload, intg["privateKey"], algorithm="RS256")
    resp = requests.post(f"https://{ims}/ims/exchange/jwt", data={
        "client_id": client_id,
        "client_secret": intg["technicalAccount"]["clientSecret"],
        "jwt_token": signed,
    }, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        sys.exit(f"IMS token exchange failed: {data}")
    return data["access_token"], data.get("expires_in", 86400)
