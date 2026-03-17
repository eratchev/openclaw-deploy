#!/usr/bin/env python3
"""
One-time OAuth setup script for Gmail. Run locally on your Mac.
Usage: python3 scripts/auth_setup.py --client-secret client_secret.json --out token.json
"""
import argparse
import json
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/contacts.readonly",
    "https://www.googleapis.com/auth/contacts.other.readonly",
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--client-secret", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    flow = InstalledAppFlow.from_client_secrets_file(args.client_secret, SCOPES)
    credentials = flow.run_local_server(port=0)

    token_data = {
        "token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_uri": credentials.token_uri,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "scopes": list(credentials.scopes),
    }
    with open(args.out, "w") as f:
        json.dump(token_data, f, indent=2)
    print(f"Token written to {args.out}")


if __name__ == "__main__":
    main()
