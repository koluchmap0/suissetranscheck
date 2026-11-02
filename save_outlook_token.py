#!/usr/bin/env python3
"""Save initial Outlook refresh token by getting a fresh one from the old token."""
import httpx

# Original refresh token from user
OLD_TOKEN = "M.C524_BAY.0.U.MsaArtifacts.-Clp82zqJl5cXnYe4MxIPJSR1oH8opy3QS581c4Mrp6o3nQO54k*NESFYeFujSmVWzdTLqsVzV7YSIxhldDYNj*07cwch*JxP3fEqEuioK0s13rjevSsAUH2WGXgzZ!*XWmlaioquFVnX6WDQGklwZN*RN2N2NA9wwsnhHqmiaPfHCGmlYcyFlIkIv!DpTN6QJPAzpaJ9CoGZqKjJzz9bWCJqhrG0SMH3SoAWeyZ6Nuipb5zrgAxGGpV0sn5osaEQjhz6GW1nCzcjJpFpuF7B3E7S!Cb8BkynWY2VmIspKRswhGm*FSbPuQks17pRKby2lf4QbKarDJZrW5seuC7BVaA17s!cBspxfKJxFwgioNkaYRYnut2RXn39zO4CvvR29ne6JVHqsXDFRblDl4v7TtM7gSm2kkdAc7cPcubEjIAgg8AofZvZoxuMQysFbiE*Zv2BdEWqDkjfcQ0v6pkBof8$"
CLIENT_ID = "9e5f94bc-e8a4-4e73-b8be-63364c29d753"

r = httpx.post("https://login.microsoftonline.com/consumers/oauth2/v2.0/token", data={
    "client_id": CLIENT_ID,
    "grant_type": "refresh_token",
    "refresh_token": OLD_TOKEN,
    "scope": "https://outlook.office.com/IMAP.AccessAsUser.All offline_access",
}, timeout=15)
resp = r.json()

if "refresh_token" in resp:
    new_rt = resp["refresh_token"]
    with open("/opt/darkvasend/.outlook_refresh_token", "w") as f:
        f.write(new_rt)
    print(f"SAVED: {len(new_rt)} chars")
    
    # Verify it works
    import imaplib, ssl
    access_token = resp["access_token"]
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    imap = imaplib.IMAP4_SSL("outlook.office365.com", 993, ssl_context=ctx, timeout=15)
    email = "kockshyresheat1@outlook.com"
    auth_string = f"user={email}\x01auth=Bearer {access_token}\x01\x01"
    imap.authenticate("XOAUTH2", lambda x: auth_string.encode())
    st, data = imap.select("Inbox", readonly=True)
    print(f"IMAP verify: {st} msgs={data}")
    imap.logout()
    print("ALL OK")
else:
    print(f"ERROR: {resp.get('error', 'unknown')}")
    print(resp.get("error_description", ""))
