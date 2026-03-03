#!/usr/bin/env python3
"""
Encrypt token.json → token.enc using a Fernet key.
Usage: python3 scripts/encrypt_token.py --token token.json --key <KEY> --out token.enc
"""
import argparse
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from auth import TokenStore
import json

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--token", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    with open(args.token) as f:
        token_data = json.load(f)

    store = TokenStore(key=args.key.encode())
    encrypted = store.encrypt(token_data)

    with open(args.out, "wb") as f:
        f.write(encrypted)
    print(f"Encrypted token written to {args.out}")

if __name__ == "__main__":
    main()
