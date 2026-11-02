import httpx, imaplib, ssl, json

email = "kockshyresheat1@outlook.com"
refresh_token = "M.C524_BAY.0.U.MsaArtifacts.-Clp82zqJl5cXnYe4MxIPJSR1oH8opy3QS581c4Mrp6o3nQO54k*NESFYeFujSmVWzdTLqsVzV7YSIxhldDYNj*07cwch*JxP3fEqEuioK0s13rjevSsAUH2WGXgzZ!*XWmlaioquFVnX6WDQGklwZN*RN2N2NA9wwsnhHqmiaPfHCGmlYcyFlIkIv!DpTN6QJPAzpaJ9CoGZqKjJzz9bWCJqhrG0SMH3SoAWeyZ6Nuipb5zrgAxGGpV0sn5osaEQjhz6GW1nCzcjJpFpuF7B3E7S!Cb8BkynWY2VmIspKRswhGm*FSbPuQks17pRKby2lf4QbKarDJZrW5seuC7BVaA17s!cBspxfKJxFwgioNkaYRYnut2RXn39zO4CvvR29ne6JVHqsXDFRblDl4v7TtM7gSm2kkdAc7cPcubEjIAgg8AofZvZoxuMQysFbiE*Zv2BdEWqDkjfcQ0v6pkBof8$"
client_id = "9e5f94bc-e8a4-4e73-b8be-63364c29d753"
token_url = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"

# Try multiple scope formats
scopes = [
    "https://outlook.office.com/IMAP.AccessAsUser.All offline_access",
    "https://outlook.office.com/.default offline_access",
    "https://outlook.office365.com/.default",
    "wl.imap offline_access",
    "offline_access https://outlook.office.com/IMAP.AccessAsUser.All",
]

for scope in scopes:
    print(f"\nTrying scope: {scope}")
    data = {
        "client_id": client_id,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "scope": scope,
    }
    r = httpx.post(token_url, data=data, timeout=15)
    resp = r.json()
    if "access_token" in resp:
        access_token = resp["access_token"]
        new_refresh = resp.get("refresh_token", "")
        print(f"  ACCESS TOKEN OK (len={len(access_token)})")
        if new_refresh:
            print(f"  NEW REFRESH: {new_refresh[:40]}...")
        
        # Test IMAP
        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            imap = imaplib.IMAP4_SSL("outlook.office365.com", 993, ssl_context=ctx, timeout=15)
            auth_str = "user=" + email + "\x01auth=Bearer " + access_token + "\x01\x01"
            imap.authenticate("XOAUTH2", lambda x: auth_str.encode())
            print("  IMAP AUTH: SUCCESS!")
            st, d = imap.select("INBOX", readonly=True)
            print(f"  INBOX: {st} msgs={d}")
            st, folders = imap.list()
            for f in folders[:10]:
                print(f"    {f.decode() if isinstance(f, bytes) else f}")
            imap.logout()
            print("  === WORKING SCOPE FOUND ===")
            break
        except Exception as e:
            print(f"  IMAP AUTH FAILED: {e}")
    else:
        err = resp.get("error", "")
        desc = resp.get("error_description", "")[:100]
        print(f"  FAILED: {err} - {desc}")
