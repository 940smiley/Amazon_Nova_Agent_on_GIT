from __future__ import annotations
import os, re, shutil, subprocess
from pathlib import Path
from typing import List, Optional
from git import Repo

SUGGESTION_RE = re.compile(r"```suggestion\n(?P<body>.*?)\n```", re.DOTALL)


def run(cmd: list[str], cwd: str | Path, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd), text=True, capture_output=True, check=check)


def clone_or_update(url: str, dest: Path) -> Repo:
    if dest.exists():
        repo = Repo(dest)
        repo.git.fetch("--all", "--prune")
        return repo
    dest.parent.mkdir(parents=True, exist_ok=True)
    return Repo.clone_from(url, dest)


def checkout_pr(repo: Repo, pr_number: int, branch_name: str) -> None:
    repo.git.fetch("origin", f"pull/{pr_number}/head:{branch_name}")
    repo.git.checkout(branch_name)


def has_changes(repo: Repo) -> bool:
    return bool(repo.git.status("--porcelain"))


def commit_all(repo: Repo, message: str) -> None:
    repo.git.add(A=True)
    if has_changes(repo):
        repo.git.commit("-m", message)


def push(repo: Repo, remote_branch: str) -> None:
    repo.git.push("origin", f"HEAD:{remote_branch}")


def extract_suggestions(comment_body: str) -> List[str]:
    return [m.group("body") for m in SUGGESTION_RE.finditer(comment_body or "")]


def apply_suggestions_best_effort(repo_path: Path, comments: list[dict]) -> int:
    """Apply simple GitHub suggestion comments when the exact old line context is unavailable.

    GitHub's REST review-comment payload includes path/position but not a full patch hunk in every case.
    This conservative implementation appends a TODO patch file for nontrivial suggestions rather than guessing.
    For exact automation, extend this with GraphQL reviewThread diff hunk data.
    """
    count = 0
    todo = repo_path / "NOVA_BOT_SUGGESTIONS.md"
    lines = []
    for c in comments:
        author = c.get("user", {}).get("login", "")
        path = c.get("path", "")
        for suggestion in extract_suggestions(c.get("body", "")):
            lines.append(f"## {path}\n\nSuggested by `{author}` in {c.get('html_url','')}\n\n```\n{suggestion}\n```\n")
            count += 1
    if lines:
        with todo.open("a", encoding="utf-8") as f:
            f.write("\n".join(lines))
    return count


def dependency_fix_commands(repo_path: Path, package_name: Optional[str] = None) -> list[list[str]]:
    cmds: list[list[str]] = []
    files = {p.name for p in repo_path.iterdir() if p.is_file()}
    if "package.json" in files:
        cmds.append(["npm", "install"])
        cmds.append(["npm", "audit", "fix"])
        if package_name:
            cmds.append(["npm", "update", package_name])
    if "requirements.txt" in files and package_name:
        cmds.append(["python", "-m", "pip", "install", "--upgrade", package_name])
    if "pyproject.toml" in files and package_name:
        cmds.append(["python", "-m", "pip", "install", "--upgrade", package_name])
    if "Gemfile" in files and package_name:
        cmds.append(["bundle", "update", package_name])
    if "go.mod" in files and package_name:
        cmds.append(["go", "get", f"{package_name}@latest"])
        cmds.append(["go", "mod", "tidy"])
    return cmds


def attempt_dependency_fix(repo: Repo, package_name: Optional[str]) -> list[str]:
    outputs: list[str] = []
    for cmd in dependency_fix_commands(Path(repo.working_tree_dir), package_name):
        try:
            p = run(cmd, repo.working_tree_dir, check=False)
            outputs.append(f"$ {' '.join(cmd)}\nexit={p.returncode}\n{p.stdout[-2000:]}\n{p.stderr[-2000:]}")
        except FileNotFoundError:
            outputs.append(f"Skipped missing tool: {cmd[0]}")
    return outputs


def conflicted_files(repo: Repo) -> list[Path]:
    root = Path(repo.working_tree_dir)
    return [root / p for p in repo.git.diff("--name-only", "--diff-filter=U").splitlines() if p.strip()]
