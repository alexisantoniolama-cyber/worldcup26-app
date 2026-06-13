import json, os
from google.oauth2 import service_account
import google.auth.transport.requests
import requests

PROJECT = "world-cup-2026-bb8ee"

creds = service_account.Credentials.from_service_account_info(
    json.loads(os.environ["FIREBASE_SERVICE_ACCOUNT"]),
    scopes=["https://www.googleapis.com/auth/firebase.messaging"])
creds.refresh(google.auth.transport.requests.Request())

msg = {"message": {
    "topic": "goals",
    "notification": {"title": os.environ["TITLE"], "body": os.environ["BODY"]},
    "android": {"priority": "high"},
}}

r = requests.post(
    f"https://fcm.googleapis.com/v1/projects/{PROJECT}/messages:send",
    headers={"Authorization": f"Bearer {creds.token}", "Content-Type": "application/json"},
    data=json.dumps(msg), timeout=30)
print(r.status_code, r.text)
r.raise_for_status()
