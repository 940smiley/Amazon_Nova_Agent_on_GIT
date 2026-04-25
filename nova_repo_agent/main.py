from __future__ import annotations

import argparse
import os
import traceback
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from .git_ops import (apply_suggestions_best_effort, attempt_dependency_fix,
                      checkout_pr, clone_or_update, commit_all, has_changes,
                      push)
from .github_client import GitHubClient
from .logger import RunLog
from .models import Finding
from .nova import NovaClient


def severity_rank(s: str) -> int:
    return {"low": 1, "medium": 2, "high": 3, "critical": 4}.get(
        (s or "low").lower(), 1
    )


def load_config(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    for key in [
        "scanning",
        "dependabot",
        "pr_comment_edits",
        "merge",
        "exports",
        "nova",
    ]:
        cfg.setdefault(key, {})
    cfg["owner"] = os.getenv("GITHUB_OWNER", cfg.get("owner", "940smiley"))
    cfg["mutation_mode"] = os.getenv(
        "NOVA_MUTATION_MODE", cfg.get("mutation_mode", "dry-run")
    )
    cfg["workdir"] = os.getenv("WORKDIR", cfg.get("workdir", ".nova-workdir"))
    return cfg


def repo_inventory(repo, alerts, issues, prs, cfg):
    max_alerts = int(cfg.get("nova", {}).get("max_alerts_in_prompt", 40))
    max_issues = int(cfg.get("nova", {}).get("max_issues_in_prompt", 30))
    max_prs = int(cfg.get("nova", {}).get("max_prs_in_prompt", 30))
    open_issues = [i for i in issues if "pull_request" not in i]
    alert_summary = []
    for a in alerts[:max_alerts]:
        alert_summary.append(
            {
                "package": a.get("dependency", {}).get("package", {}).get("name"),
                "ecosystem": a.get("dependency", {})
                .get("package", {})
                .get("ecosystem"),
                "severity": a.get("security_advisory", {}).get("severity"),
                "summary": a.get("security_advisory", {}).get("summary"),
                "state": a.get("state"),
            }
        )
    return "\n".join(
        [
            f"repo={repo.get('full_name')}",
            f"language={repo.get('language')}",
            f"default_branch={repo.get('default_branch')}",
            f"updated={repo.get('updated_at')}",
            f"open_dependabot_alerts={len(alerts)}",
            f"open_issues={len(open_issues)}",
            f"open_prs={len(prs)}",
            "alerts=" + repr(alert_summary),
            "issues=" + repr([i.get("title") for i in open_issues[:max_issues]]),
            "prs=" + repr([p.get("title") for p in prs[:max_prs]]),
        ]
    )


def authenticated_clone_url(clone_url: str, token: str) -> str:
    return (
        clone_url.replace("https://", f"https://x-access-token:{token}@")
        if clone_url.startswith("https://")
        else clone_url
    )


def scan_repo(owner, repo_data, cfg, gh, nova, log, workdir, token):
    name = repo_data["name"]
    alerts = []
    for state in cfg["dependabot"].get("states", ["open"]):
        try:
            alerts.extend(gh.dependabot_alerts(owner, name, state=state))
        except Exception as e:
            log.add(
                Finding.now(
                    owner,
                    name,
                    "dependabot",
                    "unknown",
                    "Dependabot alerts unavailable",
                    action="skipped",
                    details=str(e),
                )
            )
    issues = gh.issues(owner, name)
    prs = gh.pulls(owner, name)
    for a in alerts:
        adv = a.get("security_advisory", {})
        dep = a.get("dependency", {}).get("package", {})
        sev = adv.get("severity", "unknown")
        log.add(
            Finding.now(
                owner,
                name,
                "dependabot",
                sev,
                adv.get("summary", "Dependabot alert"),
                url=a.get("html_url", ""),
                state=a.get("state", ""),
                details=f"package={dep.get('name')} ecosystem={dep.get('ecosystem')}",
            )
        )
    for i in issues:
        if "pull_request" not in i:
            log.add(
                Finding.now(
                    owner,
                    name,
                    "issue",
                    "info",
                    i.get("title", "issue"),
                    url=i.get("html_url", ""),
                    state=i.get("state", ""),
                )
            )
    for p in prs:
        log.add(
            Finding.now(
                owner,
                name,
                "pull_request",
                "info",
                p.get("title", "pr"),
                url=p.get("html_url", ""),
                state=p.get("state", ""),
                details=f"mergeable_state={p.get('mergeable_state')}",
            )
        )
    log.suggest(
        name,
        nova.repo_suggestions(
            name, repo_inventory(repo_data, alerts, issues, prs, cfg)
        ),
    )

    mode = cfg.get("mutation_mode", "dry-run")
    if mode not in ("write", "merge"):
        return

    repo_dir = workdir / name
    repo = clone_or_update(
        authenticated_clone_url(repo_data["clone_url"], token), repo_dir
    )
    allow = set(cfg["pr_comment_edits"].get("bot_author_allowlist", []))
    for p in prs:
        prn = int(p["number"])
        try:
            branch = f"nova/pr-{prn}-suggestions"
            checkout_pr(repo, prn, branch)
            comments = gh.pr_review_comments(owner, name, prn) + gh.issue_comments(
                owner, name, prn
            )
            trusted = [c for c in comments if c.get("user", {}).get("login") in allow]
            count = apply_suggestions_best_effort(repo_dir, trusted)
            if count and has_changes(repo):
                commit_all(
                    repo, f"chore: capture Nova-applied bot suggestions for PR #{prn}"
                )
                push(repo, branch)
                log.add(
                    Finding.now(
                        owner,
                        name,
                        "pr_suggestion",
                        "info",
                        f"Captured {count} bot suggestions for PR #{prn}",
                        action="pushed",
                        url=p.get("html_url", ""),
                    )
                )
        except Exception as e:
            log.add(
                Finding.now(
                    owner,
                    name,
                    "pr_suggestion",
                    "warning",
                    f"PR #{prn} suggestion handling failed",
                    action="failed",
                    url=p.get("html_url", ""),
                    details=str(e),
                )
            )

    floor = severity_rank(cfg["dependabot"].get("severity_floor", "low"))
    for a in alerts:
        sev = a.get("security_advisory", {}).get("severity", "low")
        if severity_rank(sev) < floor:
            continue
        dep_name = a.get("dependency", {}).get("package", {}).get("name")
        safe_dep = (dep_name or "deps").replace("@", "").replace("/", "-")
        branch = f"{cfg['dependabot'].get('branch_prefix', 'nova/dependabot-fix')}/{safe_dep}"
        base = repo_data.get("default_branch", "main")
        try:
            repo.git.fetch("origin", base)
            repo.git.checkout(base)
            repo.git.pull("origin", base)
            repo.git.checkout("-B", branch)
            outputs = attempt_dependency_fix(repo, dep_name)
            if has_changes(repo):
                commit_all(
                    repo,
                    f"fix: update dependency for Dependabot alert {dep_name or ''}".strip(),
                )
                push(repo, branch)
                try:
                    pr = gh.create_pr(
                        owner,
                        name,
                        f"fix: resolve Dependabot alert for {dep_name}",
                        branch,
                        base,
                        "Automated Nova security dependency update. Review CI before merge.",
                    )
                    log.add(
                        Finding.now(
                            owner,
                            name,
                            "dependabot_fix",
                            sev,
                            f"Created dependency fix PR for {dep_name}",
                            action="opened_pr",
                            url=pr.get("html_url", ""),
                            details="\n".join(outputs),
                        )
                    )
                except Exception as e:
                    log.add(
                        Finding.now(
                            owner,
                            name,
                            "dependabot_fix",
                            sev,
                            f"Dependency fix branch pushed for {dep_name}",
                            action="branch_pushed",
                            details=str(e) + "\n" + "\n".join(outputs),
                        )
                    )
            else:
                log.add(
                    Finding.now(
                        owner,
                        name,
                        "dependabot_fix",
                        sev,
                        f"No local dependency changes produced for {dep_name}",
                        action="skipped",
                        details="\n".join(outputs),
                    )
                )
        except Exception as e:
            log.add(
                Finding.now(
                    owner,
                    name,
                    "dependabot_fix",
                    sev,
                    f"Dependency fix failed for {dep_name}",
                    action="failed",
                    details=str(e),
                )
            )

    if mode == "merge":
        for p in gh.pulls(owner, name):
            pr_number = int(p["number"])
            try:
                pr = gh.pull(owner, name, pr_number)
                if pr.get("draft"):
                    continue
                if pr.get("mergeable") is True and pr.get("mergeable_state") in {
                    "clean",
                    "has_hooks",
                    "unstable",
                }:
                    if cfg["merge"].get("require_green_checks", True):
                        status = gh.combined_status(owner, name, pr["head"]["sha"])
                        if status.get("state") not in {"success", "pending"}:
                            log.add(
                                Finding.now(
                                    owner,
                                    name,
                                    "merge",
                                    "info",
                                    f"PR #{pr_number} not merged; checks={status.get('state')}",
                                    action="skipped",
                                    url=pr.get("html_url", ""),
                                )
                            )
                            continue
                    gh.merge_pr(
                        owner, name, pr_number, cfg["merge"].get("strategy", "squash")
                    )
                    log.add(
                        Finding.now(
                            owner,
                            name,
                            "merge",
                            "info",
                            f"Merged PR #{pr_number}",
                            action="merged",
                            url=pr.get("html_url", ""),
                        )
                    )
                else:
                    log.add(
                        Finding.now(
                            owner,
                            name,
                            "merge",
                            "info",
                            f"PR #{pr_number} not cleanly mergeable",
                            action="skipped",
                            url=pr.get("html_url", ""),
                            details=f"mergeable={pr.get('mergeable')} state={pr.get('mergeable_state')}",
                        )
                    )
            except Exception as e:
                log.add(
                    Finding.now(
                        owner,
                        name,
                        "merge",
                        "warning",
                        f"PR #{pr_number} merge failed",
                        action="failed",
                        url=p.get("html_url", ""),
                        details=str(e),
                    )
                )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()
    load_dotenv()
    cfg = load_config(args.config)
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        raise SystemExit(
            "GITHUB_TOKEN is required. Add it to .env or export it in your shell."
        )
    owner = cfg["owner"]
    mode = cfg.get("mutation_mode", "dry-run")
    workdir = Path(cfg.get("workdir", ".nova-workdir")).resolve()
    gh = GitHubClient(token)
    nova = NovaClient(
        os.getenv("NOVA_MODEL_ID", "amazon.nova-pro-v1:0"),
        os.getenv("AWS_REGION", "us-east-1"),
        enabled=bool(cfg.get("nova", {}).get("enabled", True)),
    )
    log = RunLog()
    print("Nova Repo Maintainer Agent")
    print(f"owner={owner} mode={mode} workdir={workdir}")
    try:
        repos = gh.list_user_repos(owner)
    except Exception as e:
        raise SystemExit(f"Failed to list repos for {owner}: {e}") from e
    scanning_cfg = cfg.get("scanning", {})
    repos = [
        r
        for r in repos
        if scanning_cfg.get("include_archived", False) or not r.get("archived")
    ]
    repos = [
        r
        for r in repos
        if scanning_cfg.get("include_forks", False) or not r.get("fork")
    ]
    max_repos = int(scanning_cfg.get("max_repos", 0) or 0)
    if max_repos:
        repos = repos[:max_repos]
    print(f"repos_to_scan={len(repos)}")
    for repo_data in repos:
        name = repo_data["name"]
        full = repo_data["full_name"]
        print(f"\n=== scanning {full} ===")
        try:
            scan_repo(owner, repo_data, cfg, gh, nova, log, workdir, token)
        except Exception as e:
            print(f"\n[ERROR] Repo {full} failed:")
            print(traceback.format_exc())
            log.add(
                Finding.now(
                    owner,
                    name,
                    "repo",
                    "error",
                    "Repository scan failed",
                    action="failed",
                    details=f"{type(e).__name__}: {e}",
                )
            )
    out_csv = Path(cfg["exports"].get("csv_log", "nova_scan_log.csv"))
    out_txt = Path(cfg["exports"].get("suggestions", "Nova_suggests.txt"))
    log.export_csv(out_csv)
    log.export_suggestions(out_txt)
    print(f"\nExported {out_csv} and {out_txt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

