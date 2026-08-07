#!/usr/bin/env python3
"""Fetch yearly commit contribution languages for the profile SVG.

Uses the same GitHub GraphQL contribution source as github-profile-3d-contrib,
but keeps more languages available for the local post-processor.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path

GRAPHQL_URL = "https://api.github.com/graphql"


def fetch_languages(user: str, year: int, token: str) -> dict:
    query = """
    query($login: String!) {
      user(login: $login) {
        contributionsCollection(
          from: \"%d-01-01T00:00:00.000Z\"
          to: \"%d-12-31T23:59:59.000Z\"
        ) {
          totalCommitContributions
          commitContributionsByRepository(maxRepositories: 100) {
            repository { primaryLanguage { name } }
            contributions { totalCount }
          }
        }
      }
    }
    """ % (year, year)
    payload = json.dumps({"query": query, "variables": {"login": user}}).encode("utf-8")
    request = urllib.request.Request(
        GRAPHQL_URL,
        data=payload,
        headers={
            "Authorization": f"bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "pestoura-profile-workflow",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"GitHub GraphQL HTTP {exc.code}: {body}") from exc

    if data.get("errors"):
        message = data["errors"][0].get("message", "unknown GraphQL error")
        raise RuntimeError(message)
    user_data = data.get("data", {}).get("user")
    if not user_data:
        raise RuntimeError(f"GitHub user not found: {user}")

    collection = user_data["contributionsCollection"]
    aggregate: dict[str, int] = defaultdict(int)
    unattributed = 0
    for item in collection.get("commitContributionsByRepository", []):
        count = int(item.get("contributions", {}).get("totalCount", 0) or 0)
        language = (item.get("repository", {}).get("primaryLanguage") or {}).get("name")
        if language:
            aggregate[language] += count
        else:
            unattributed += count

    languages = [
        {"language": language, "contributions": contributions}
        for language, contributions in sorted(
            aggregate.items(), key=lambda pair: (-pair[1], pair[0].lower())
        )
        if contributions > 0
    ]
    return {
        "user": user,
        "year": year,
        "total_commit_contributions": int(collection.get("totalCommitContributions", 0) or 0),
        "unattributed_contributions": unattributed,
        "languages": languages,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", required=True)
    parser.add_argument("--year", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise RuntimeError("GITHUB_TOKEN is required")
    if not 2008 <= args.year <= 2100:
        raise ValueError("year outside supported GitHub contribution range")

    result = fetch_languages(args.user, args.year, token)
    if not result["languages"]:
        raise RuntimeError("no attributed contribution languages returned")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"Fetched {len(result['languages'])} languages for {args.user} ({args.year}); "
        f"top chart can show up to 10 plus residual."
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
