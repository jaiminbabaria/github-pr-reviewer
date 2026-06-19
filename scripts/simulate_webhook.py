#!/usr/bin/env python3
"""Simulate a signed GitHub `pull_request` webhook against a running instance.

This lets you exercise the full pipeline (FastAPI -> SNS -> SQS -> Lambda ->
GitHub/OpenAI) without opening a real PR, OR just verify the webhook receiver
locally.

Usage:
    # against local dev server
    GITHUB_WEBHOOK_SECRET=yoursecret python scripts/simulate_webhook.py \
        --url http://127.0.0.1:8000/webhook/github

    # against your deployed EC2 box
    GITHUB_WEBHOOK_SECRET=yoursecret python scripts/simulate_webhook.py \
        --url https://your.domain.com/webhook/github \
        --repo octocat/hello-world --pr 42 --installation 12345 --sha <commit_sha>

Notes:
  * For the Lambda half to do real work, --repo / --installation / --sha must be
    real (the App must be installed on that repo and the SHA must be the PR head).
  * For just testing the receiver + signature + SNS publish, the defaults are fine.
"""
import argparse
import hashlib
import hmac
import json
import os
import sys
import urllib.request


def build_payload(repo: str, pr: int, installation: int, sha: str, title: str, author: str) -> dict:
    return {
        "action": "opened",
        "number": pr,
        "pull_request": {
            "number": pr,
            "title": title,
            "user": {"login": author},
            "head": {"sha": sha},
            "base": {"sha": "0" * 40},
        },
        "repository": {
            "full_name": repo,
            "id": 1234567,
            "name": repo.split("/")[-1],
        },
        "installation": {"id": installation},
        "sender": {"login": author},
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Simulate a GitHub PR webhook.")
    p.add_argument("--url", default="http://127.0.0.1:8000/webhook/github")
    p.add_argument("--repo", default="octocat/hello-world")
    p.add_argument("--pr", type=int, default=1)
    p.add_argument("--installation", type=int, default=99999999)
    p.add_argument("--sha", default="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    p.add_argument("--title", default="Simulated PR: add feature")
    p.add_argument("--author", default="octocat")
    p.add_argument("--event", default="pull_request", help="X-GitHub-Event header value")
    p.add_argument("--bad-signature", action="store_true", help="send an invalid signature (expect 401)")
    args = p.parse_args()

    secret = os.environ.get("GITHUB_WEBHOOK_SECRET")
    if not secret:
        print("ERROR: set GITHUB_WEBHOOK_SECRET to match your server config.", file=sys.stderr)
        return 2

    body = json.dumps(
        build_payload(args.repo, args.pr, args.installation, args.sha, args.title, args.author)
    ).encode()

    if args.bad_signature:
        signature = "sha256=" + "0" * 64
    else:
        signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    req = urllib.request.Request(
        args.url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": signature,
            "X-GitHub-Event": args.event,
            "X-GitHub-Delivery": "simulated-" + args.sha[:8],
            "User-Agent": "GitHub-Hookshot/simulator",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            print(f"HTTP {resp.status}")
            print(resp.read().decode())
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}")
        print(e.read().decode())
        return 1
    except urllib.error.URLError as e:
        print(f"Connection failed: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
