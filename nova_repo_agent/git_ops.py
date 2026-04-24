from __future__ import annotations
import subprocess
from pathlib import Path
from git import Repo

def clone_or_update(clone_url: str, repo_dir: Path) -> Repo:
    repo_dir.parent.mkdir(parents=True, exist_ok=True)
    if repo_dir.exists() and (repo_dir / ".git").exists():
        repo = Repo(repo_dir)
        repo.git.fetch("--all", "--prune")
        return repo
    return Repo.clone_from(clone_url, repo_dir)

def checkout_pr(repo: Repo, pr_number: int, branch: str):
    repo.git.fetch("origin", f"pull/{pr_number}/head:{branch}", force=True)
    repo.git.checkout(branch)

def has_changes(repo: Repo) -> bool:
    return bool(repo.git.status("--porcelain").strip())

def commit_all(repo: Repo, message: str):
    repo.git.add(A=True)
    if has_changes(repo):
        repo.git.commit("-m", message)

def push(repo: Repo, branch: str):
    repo.git.push("--set-upstream", "origin", branch, force_with_lease=True)

def _run(cmd, cwd):
    p = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, shell=False)
    return p.returncode, (p.stdout or "") + (p.stderr or "")

def _detect_package_manager(repo_dir: Path):
    if (repo_dir / "pnpm-lock.yaml").exists(): return "pnpm"
    if (repo_dir / "yarn.lock").exists(): return "yarn"
    if (repo_dir / "package-lock.json").exists(): return "npm"
    if (repo_dir / "requirements.txt").exists(): return "pip"
    if (repo_dir / "pyproject.toml").exists(): return "python"
    return None

def attempt_dependency_fix(repo: Repo, dep_name: str | None):
    repo_dir = Path(repo.working_tree_dir)
    outputs = []
    manager = _detect_package_manager(repo_dir)
    if not dep_name:
        return ["No dependency name available; skipped."]
    if manager == "npm":
        return [_run(["npm", "install", f"{dep_name}@latest"], repo_dir)[1]]
    if manager == "pnpm":
        return [_run(["pnpm", "add", f"{dep_name}@latest"], repo_dir)[1]]
    if manager == "yarn":
        return [_run(["yarn", "add", f"{dep_name}@latest"], repo_dir)[1]]
    if manager in {"pip", "python"}:
        return ["Python dependency update automation not implemented; use uv/poetry/pip-tools-specific workflow."]
    return ["No supported package manager detected."]

def apply_suggestions_best_effort(repo_dir: Path, comments: list[dict]) -> int:
    count = 0
    notes = []
    for c in comments:
        body = c.get("body", "") or ""
        user = c.get("user", {}).get("login", "")
        if "```suggestion" in body or "```diff" in body:
            count += 1
            notes.append(f"- {user}: {c.get('html_url','')}")
    if notes:
        (repo_dir / "NOVA_APPLIED_SUGGESTIONS.md").write_text("# Nova captured bot suggestions\n\n" + "\n".join(notes) + "\n", encoding="utf-8")
    return count
