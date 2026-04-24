from __future__ import annotations
import argparse, os, sys
from pathlib import Path
from typing import Any
import yaml
from dotenv import load_dotenv

from .github_client import GitHubClient
from .git_ops import clone_or_update, checkout_pr, apply_suggestions_best_effort, attempt_dependency_fix, commit_all, push, has_changes, conflicted_files
from .logger import RunLog
from .models import Finding
from .nova import NovaClient


def severity_rank(s: str) -> int:
    return {"low": 1, "medium": 2, "high": 3, "critical": 4}.get((s or "low").lower(), 1)


def load_config(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    cfg["owner"] = os.getenv("GITHUB_OWNER", cfg.get("owner", "940smiley"))
    cfg["mutation_mode"] = os.getenv("NOVA_MUTATION_MODE", cfg.get("mutation_mode", "dry-run"))
    cfg["workdir"] = os.getenv("WORKDIR", cfg.get("workdir", ".nova-workdir"))
    return cfg


def repo_inventory(repo: dict, alerts: list, issues: list, prs: list) -> str:
    return "\n".join([
        f"repo={repo.get('full_name')} language={repo.get('language')} updated={repo.get('updated_at')}",
        f"open_dependabot_alerts={len(alerts)}",
        f"open_issues={len([i for i in issues if 'pull_request' not in i])}",
        f"open_prs={len(prs)}",
        "alerts=" + str([{
            "package": a.get("dependency", {}).get("package", {}).get("name"),
            "ecosystem": a.get("dependency", {}).get("package", {}).get("ecosystem"),
            "severity": a.get("security_advisory", {}).get("severity"),
            "summary": a.get("security_advisory", {}).get("summary"),
        } for a in alerts[:30]]),
        "issues=" + str([i.get("title") for i in issues[:30] if "pull_request" not in i]),
        "prs=" + str([p.get("title") for p in prs[:30]]),
    ])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()

    load_dotenv()
    cfg = load_config(args.config)
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        raise SystemExit("GITHUB_TOKEN is required")

    owner = cfg["owner"]
    mode = cfg.get("mutation_mode", "dry-run")
    gh = GitHubClient(token)
    nova = NovaClient(os.getenv("NOVA_MODEL_ID", "amazon.nova-pro-v1:0"), os.getenv("AWS_REGION", "us-east-1"))
    log = RunLog()
    workdir = Path(cfg.get("workdir", ".nova-workdir")).resolve()

    repos = gh.list_user_repos(owner)
    repos = [r for r in repos if (cfg["scanning"].get("include_archived") or not r.get("archived"))]
    repos = [r for r in repos if (cfg["scanning"].get("include_forks") or not r.get("fork"))]
    max_repos = int(cfg["scanning"].get("max_repos", 0) or 0)
    if max_repos:
        repos = repos[:max_repos]

    for r in repos:
        name = r["name"]
        full = r["full_name"]
        print(f"\n=== scanning {full} ===")
        try:
            alerts = []
            for state in cfg["dependabot"].get("states", ["open"]):
                try:
                    alerts.extend(gh.dependabot_alerts(owner, name, state=state))
                except Exception as e:
                    log.add(Finding.now(owner, name, "dependabot", "unknown", "Dependabot alerts unavailable", action="skipped", details=str(e)))
            issues = gh.issues(owner, name)
            prs = gh.pulls(owner, name)

            for a in alerts:
                adv = a.get("security_advisory", {})
                dep = a.get("dependency", {}).get("package", {})
                sev = adv.get("severity", "unknown")
                log.add(Finding.now(owner, name, "dependabot", sev, adv.get("summary", "Dependabot alert"), url=a.get("html_url", ""), state=a.get("state", ""), details=f"package={dep.get('name')} ecosystem={dep.get('ecosystem')}"))

            for i in issues:
                if "pull_request" not in i:
                    log.add(Finding.now(owner, name, "issue", "info", i.get("title", "issue"), url=i.get("html_url", ""), state=i.get("state", "")))

            for p in prs:
                log.add(Finding.now(owner, name, "pull_request", "info", p.get("title", "pr"), url=p.get("html_url", ""), state=p.get("state", ""), details=f"mergeable_state={p.get('mergeable_state')}"))

            log.suggest(name, nova.repo_suggestions(name, repo_inventory(r, alerts, issues, prs)))

            if mode in ("write", "merge"):
                repo_dir = workdir / name
                repo = clone_or_update(r["clone_url"].replace("https://", f"https://x-access-token:{token}@"), repo_dir)

                # Pass 2A: apply trusted bot PR suggestions.
                allow = set(cfg["pr_comment_edits"].get("bot_author_allowlist", []))
                for p in prs:
                    prn = int(p["number"])
                    branch = f"nova/pr-{prn}-suggestions"
                    checkout_pr(repo, prn, branch)
                    comments = gh.pr_review_comments(owner, name, prn) + gh.issue_comments(owner, name, prn)
                    trusted = [c for c in comments if c.get("user", {}).get("login") in allow]
                    count = apply_suggestions_best_effort(repo_dir, trusted)
                    if count and has_changes(repo):
                        commit_all(repo, f"chore: capture Nova-applied bot suggestions for PR #{prn}")
                        push(repo, branch)
                        log.add(Finding.now(owner, name, "pr_suggestion", "info", f"Captured {count} bot suggestions for PR #{prn}", action="pushed", url=p.get("html_url", "")))

                # Pass 2B: dependency alert fix branches.
                floor = severity_rank(cfg["dependabot"].get("severity_floor", "low"))
                for a in alerts:
                    sev = a.get("security_advisory", {}).get("severity", "low")
                    if severity_rank(sev) < floor:
                        continue
                    dep_name = a.get("dependency", {}).get("package", {}).get("name")
                    branch = f"{cfg['dependabot'].get('branch_prefix','nova/dependabot-fix')}/{dep_name or 'deps'}".replace("@", "")
                    base = r.get("default_branch", "main")
                    repo.git.checkout(base)
                    repo.git.pull("origin", base)
                    repo.git.checkout("-B", branch)
                    outputs = attempt_dependency_fix(repo, dep_name)
                    if has_changes(repo):
                        commit_all(repo, f"fix: update dependency for Dependabot alert {dep_name or ''}".strip())
                        push(repo, branch)
                        try:
                            pr = gh.create_pr(owner, name, f"fix: resolve Dependabot alert for {dep_name}", branch, base, "Automated Nova security dependency update. Review CI before merge.")
                            log.add(Finding.now(owner, name, "dependabot_fix", sev, f"Created dependency fix PR for {dep_name}", action="opened_pr", url=pr.get("html_url", ""), details="\n".join(outputs)))
                        except Exception as e:
                            log.add(Finding.now(owner, name, "dependabot_fix", sev, f"Dependency fix branch pushed for {dep_name}", action="branch_pushed", details=str(e)))

                # Pass 2C: merge eligible PRs.
                if mode == "merge":
                    for p in gh.pulls(owner, name):
                        pr = gh.pull(owner, name, int(p["number"]))
                        if pr.get("draft"):
                            continue
                        if pr.get("mergeable") is True and pr.get("mergeable_state") in {"clean", "has_hooks", "unstable"}:
                            if cfg["merge"].get("require_green_checks", True):
                                status = gh.combined_status(owner, name, pr["head"]["sha"])
                                if status.get("state") not in {"success", "pending"}:
                                    log.add(Finding.now(owner, name, "merge", "info", f"PR #{p['number']} not merged; checks={status.get('state')}", action="skipped", url=pr.get("html_url", "")))
                                    continue
                            try:
                                gh.merge_pr(owner, name, int(p["number"]), cfg["merge"].get("strategy", "squash"))
                                log.add(Finding.now(owner, name, "merge", "info", f"Merged PR #{p['number']}", action="merged", url=pr.get("html_url", "")))
                            except Exception as e:
                                log.add(Finding.now(owner, name, "merge", "warning", f"PR #{p['number']} merge failed", action="failed", url=pr.get("html_url", ""), details=str(e)))
                        else:
                            log.add(Finding.now(owner, name, "merge", "info", f"PR #{p['number']} not cleanly mergeable", action="skipped", url=pr.get("html_url", ""), details=f"mergeable={pr.get('mergeable')} state={pr.get('mergeable_state')}"))

        except Exception as e:
            log.add(Finding.now(owner, name, "repo", "error", "Repository scan failed", action="failed", details=str(e)))

    out_csv = Path(cfg["exports"].get("csv_log", "nova_scan_log.csv"))
    out_txt = Path(cfg["exports"].get("suggestions", "Nova_suggests.txt"))
    log.export_csv(out_csv)
    log.export_suggestions(out_txt)
    print(f"\nExported {out_csv} and {out_txt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
