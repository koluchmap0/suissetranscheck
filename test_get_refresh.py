import httpx, json

refresh_token = "M.C524_BAY.0.U.MsaArtifacts.-Clp82zqJl5cXnYe4MxIPJSR1oH8opy3QS581c4Mrp6o3nQO54k*NESFYeFujSmVWzdTLqsVzV7YSIxhldDYNj*07cwch*JxP3fEqEuioK0s13rjevSsAUH2WGXgzZ!*XWmlaioquFVnX6WDQGklwZN*RN2N2NA9wwsnhHqmiaPfHCGmlYcyFlIkIv!DpTN6QJPAzpaJ9CoGZqKjJzz9bWCJqhrG0SMH3SoAWeyZ6Nuipb5zrgAxGGpV0sn5osaEQjhz6GW1nCzcjJpFpuF7B3E7S!Cb8BkynWY2VmIspKRswhGm*FSbPuQks17pRKby2lf4QbKarDJZrW5seuC7BVaA17s!cBspxfKJxFwgioNkaYRYnut2RXn39zO4CvvR29ne6JVHqsXDFRblDl4v7TtM7gSm2kkdAc7cPcubEjIAgg8AofZvZoxuMQysFbiE*Zv2BdEWqDkjfcQ0v6pkBof8$"
client_id = "9e5f94bc-e8a4-4e73-b8be-63364c29d753"

r = httpx.post("https://login.microsoftonline.com/consumers/oauth2/v2.0/token", data={
    "client_id": client_id,
    "grant_type": "refresh_token",
    "refresh_token": refresh_token,
    "scope": "https://outlook.office.com/IMAP.AccessAsUser.All offline_access",
}, timeout=15)
resp = r.json()
if "refresh_token" in resp:
    print("NEW_REFRESH=" + resp["refresh_token"])
    print("ACCESS_LEN=" + str(len(resp["access_token"])))
else:
    print("ERROR")
    print(json.dumps(resp))
