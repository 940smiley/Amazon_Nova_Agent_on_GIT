from __future__ import annotations
import time
from typing import Any, Dict, Generator, Iterable, List, Optional
import requests


class GitHubClient:
    def __init__(self, token: str, api_base: str = "https://api.github.com") -> None:
        self.api_base = api_base.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "nova-repo-maintainer-agent",
        })

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = path if path.startswith("http") else f"{self.api_base}{path}"
        while True:
            r = self.session.request(method, url, timeout=60, **kwargs)
            if r.status_code == 403 and r.headers.get("X-RateLimit-Remaining") == "0":
                reset = int(r.headers.get("X-RateLimit-Reset", "0"))
                sleep_for = max(1, reset - int(time.time()) + 1)
                print(f"GitHub rate limit reached; sleeping {sleep_for}s")
                time.sleep(sleep_for)
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"GitHub {method} {url} failed: {r.status_code} {r.text[:500]}")
            if r.status_code == 204:
                return None
            return r.json()

    def paginate(self, path: str, params: Optional[dict] = None) -> Generator[Any, None, None]:
        url = f"{self.api_base}{path}"
        while url:
            r = self.session.get(url, params=params, timeout=60)
            params = None
            if r.status_code >= 400:
                raise RuntimeError(f"GitHub GET {url} failed: {r.status_code} {r.text[:500]}")
            data = r.json()
            if isinstance(data, list):
                yield from data
            else:
                yield data
            url = r.links.get("next", {}).get("url")

    def list_user_repos(self, owner: str) -> List[dict]:
        return list(self.paginate(f"/users/{owner}/repos", {"per_page": 100, "sort": "updated"}))

    def dependabot_alerts(self, owner: str, repo: str, state: str = "open") -> List[dict]:
        return list(self.paginate(f"/repos/{owner}/{repo}/dependabot/alerts", {"state": state, "per_page": 100}))

    def issues(self, owner: str, repo: str) -> List[dict]:
        return list(self.paginate(f"/repos/{owner}/{repo}/issues", {"state": "open", "per_page": 100}))

    def pulls(self, owner: str, repo: str) -> List[dict]:
        return list(self.paginate(f"/repos/{owner}/{repo}/pulls", {"state": "open", "per_page": 100}))

    def pull(self, owner: str, repo: str, number: int) -> dict:
        return self.request("GET", f"/repos/{owner}/{repo}/pulls/{number}")

    def pr_review_comments(self, owner: str, repo: str, number: int) -> List[dict]:
        return list(self.paginate(f"/repos/{owner}/{repo}/pulls/{number}/comments", {"per_page": 100}))

    def issue_comments(self, owner: str, repo: str, number: int) -> List[dict]:
        return list(self.paginate(f"/repos/{owner}/{repo}/issues/{number}/comments", {"per_page": 100}))

    def combined_status(self, owner: str, repo: str, ref: str) -> dict:
        return self.request("GET", f"/repos/{owner}/{repo}/commits/{ref}/status")

    def merge_pr(self, owner: str, repo: str, number: int, method: str = "squash") -> dict:
        return self.request("PUT", f"/repos/{owner}/{repo}/pulls/{number}/merge", json={"merge_method": method})

    def create_pr(self, owner: str, repo: str, title: str, head: str, base: str, body: str) -> dict:
        return self.request("POST", f"/repos/{owner}/{repo}/pulls", json={"title": title, "head": head, "base": base, "body": body})
