# Nova Repo Maintainer Agent

Local Python agent to scan GitHub repositories for Dependabot alerts, issues, PRs, and export logs. Default mode is dry-run.

## Windows Git Bash

```bash
python -m venv .venv
source .venv/Scripts/activate
python -m pip install -r requirements.txt
cp .env.example .env
# edit .env
python -m nova_repo_agent.main --config config.yaml
```

Required GitHub token scopes: repo, security_events, read:org if needed.
