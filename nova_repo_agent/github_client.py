from __future__ import annotations
import time
import requests

class GitHubClient:
    def __init__(self, token: str):
        self.base = "https://api.github.com"
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "nova-repo-maintainer-agent",
        })

    def _request(self, method, path_or_url, **kwargs):
        url = path_or_url if path_or_url.startswith("http") else self.base + path_or_url
        r = self.session.request(method, url, timeout=60, **kwargs)
        return r

    def get(self, path, params=None):
        r = self._request("GET", path, params=params or {})
        if not r.ok:
            raise RuntimeError(f"GitHub GET {r.url} failed: {r.status_code} {r.text[:1000]}")
        return r.json()

    def post(self, path, json=None):
        r = self._request("POST", path, json=json or {})
        if not r.ok:
            raise RuntimeError(f"GitHub POST {r.url} failed: {r.status_code} {r.text[:1000]}")
        return r.json() if r.text else {}

    def put(self, path, json=None):
        r = self._request("PUT", path, json=json or {})
        if not r.ok:
            raise RuntimeError(f"GitHub PUT {r.url} failed: {r.status_code} {r.text[:1000]}")
        return r.json() if r.text else {}

    def paginate(self, path, params=None):
        url = self.base + path
        params = dict(params or {})
        while url:
            r = self._request("GET", url, params=params)
            if not r.ok:
                raise RuntimeError(f"GitHub GET {r.url} failed: {r.status_code} {r.text[:1000]}")
            data = r.json()
            if isinstance(data, list):
                yield from data
            else:
                yield data
            link = r.headers.get("Link", "")
            nxt = None
            for part in link.split(","):
                if 'rel="next"' in part:
                    nxt = part[part.find("<")+1:part.find(">")]
                    break
            url = nxt
            params = {}

    def list_user_repos(self, owner): return list(self.paginate(f"/users/{owner}/repos", {"per_page":100,"sort":"updated"}))
    def dependabot_alerts(self, owner, repo, state="open"): return list(self.paginate(f"/repos/{owner}/{repo}/dependabot/alerts", {"per_page":100,"state":state}))
    def issues(self, owner, repo): return list(self.paginate(f"/repos/{owner}/{repo}/issues", {"per_page":100,"state":"open"}))
    def pulls(self, owner, repo): return list(self.paginate(f"/repos/{owner}/{repo}/pulls", {"per_page":100,"state":"open"}))
    def pull(self, owner, repo, number): return self.get(f"/repos/{owner}/{repo}/pulls/{number}")
    def pr_review_comments(self, owner, repo, number): return list(self.paginate(f"/repos/{owner}/{repo}/pulls/{number}/comments", {"per_page":100}))
    def issue_comments(self, owner, repo, number): return list(self.paginate(f"/repos/{owner}/{repo}/issues/{number}/comments", {"per_page":100}))
    def create_pr(self, owner, repo, title, head, base, body): return self.post(f"/repos/{owner}/{repo}/pulls", {"title":title,"head":head,"base":base,"body":body})
    def combined_status(self, owner, repo, sha): return self.get(f"/repos/{owner}/{repo}/commits/{sha}/status")
    def merge_pr(self, owner, repo, number, strategy="squash"): return self.put(f"/repos/{owner}/{repo}/pulls/{number}/merge", {"merge_method":strategy})
