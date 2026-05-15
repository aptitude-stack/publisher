"""GitHub API integration helpers for repository enrichment."""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


def fetch_repository_signals(repo_url: str | None) -> dict[str, Any]:
    """Fetch lightweight GitHub repository signals for a supported repo URL."""
    if not repo_url:
        return {
            "available": False,
            "reason": "no_repo_url",
        }

    owner_repo = _parse_github_owner_repo(repo_url)
    if owner_repo is None:
        return {
            "available": False,
            "reason": "unsupported_repo_provider",
            "repo_url": repo_url,
        }

    owner, repo = owner_repo
    repo_api_url = f"https://api.github.com/repos/{owner}/{repo}"
    contributors_api_url = f"{repo_api_url}/contributors?per_page=1&anon=true"

    try:
        repo_payload, _ = _http_get_json(repo_api_url)
        _, contributor_headers = _http_get_json(contributors_api_url)
    except (HTTPError, URLError, TimeoutError, ValueError) as exc:
        return {
            "available": False,
            "reason": "fetch_failed",
            "repo_url": repo_url,
            "error": str(exc),
        }

    contributors_count = _parse_contributors_count(contributor_headers)
    return {
        "available": True,
        "provider": "github",
        "repo_url": repo_url,
        "owner": owner,
        "owner_type": ((repo_payload.get("owner") or {}).get("type"))
        if isinstance(repo_payload.get("owner"), dict)
        else None,
        "repo": repo,
        "stars": repo_payload.get("stargazers_count"),
        "forks": repo_payload.get("forks_count"),
        "watchers": repo_payload.get("subscribers_count", repo_payload.get("watchers_count")),
        "open_issues": repo_payload.get("open_issues_count"),
        "default_branch": repo_payload.get("default_branch"),
        "created_at": repo_payload.get("created_at"),
        "updated_at": repo_payload.get("updated_at"),
        "pushed_at": repo_payload.get("pushed_at"),
        "contributors_count": contributors_count,
        "description": repo_payload.get("description"),
        "license": (repo_payload.get("license") or {}).get("spdx_id")
        if isinstance(repo_payload.get("license"), dict)
        else None,
    }


def _parse_github_owner_repo(repo_url: str) -> tuple[str, str] | None:
    """Parse a GitHub repository URL into owner/repo."""
    parsed = urlparse(repo_url)
    if parsed.scheme in {"http", "https"} and parsed.netloc.lower() == "github.com":
        path = parsed.path.strip("/")
        parts = [part for part in path.split("/") if part]
        if len(parts) >= 2:
            return parts[0], parts[1].removesuffix(".git")

    ssh_match = re.fullmatch(r"git@github\.com:([^/]+)/(.+?)(?:\.git)?", repo_url)
    if ssh_match:
        return ssh_match.group(1), ssh_match.group(2)

    return None


def _http_get_json(url: str) -> tuple[dict[str, Any], dict[str, str]]:
    """Fetch a JSON document from an HTTP endpoint."""
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "aptitude-publisher",
        },
    )
    with urlopen(request, timeout=5) as response:
        body = response.read().decode("utf-8")
        payload = json.loads(body)
        if not isinstance(payload, dict) and not isinstance(payload, list):
            raise ValueError("Expected JSON response payload.")
        headers = dict(response.headers.items())
    if isinstance(payload, list):
        payload = {"items": payload}
    return payload, headers


def _parse_contributors_count(headers: dict[str, str]) -> int | None:
    """Estimate contributors count from the GitHub Link header."""
    link_header = headers.get("Link") or headers.get("link")
    if not link_header:
        return None

    match = re.search(r"[?&]page=(\d+)>; rel=\"last\"", link_header)
    if not match:
        return 1
    return int(match.group(1))
