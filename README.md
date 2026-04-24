# Nova Repo Maintainer Agent

Local Amazon Nova-powered GitHub maintenance agent for `github.com/940smiley`.

It scans repositories, logs open Dependabot alerts/issues/PRs, applies GitHub suggestion comments from trusted bot authors, attempts dependency updates for Dependabot alerts, merges mergeable PRs, optionally resolves simple conflicts, and exports:

- `nova_scan_log.csv`
- `Nova_suggests.txt`

## Safety model

Default mode is `dry-run`; it reads and logs only. Mutation requires explicit config or environment:

```bash
export NOVA_MUTATION_MODE=write   # push fix commits / branches, no merge
export NOVA_MUTATION_MODE=merge   # push fixes and merge eligible PRs
```

The agent will not bypass protected branches, failing required checks, or human-review requirements.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env and config.yaml
python -m nova_repo_agent.main --config config.yaml
```

## Required GitHub token permissions

For fine-grained PATs, grant access to the selected repos and include repository permissions needed for:

- Contents: read/write
- Pull requests: read/write
- Issues: read/write
- Metadata: read
- Dependabot alerts: read
- Code scanning/security events where applicable

## AWS permissions

The AWS identity needs `bedrock:InvokeModel` for the configured Amazon Nova model.

## Notes

- GitHub suggestion comments are applied only when they contain fenced `suggestion` blocks and map cleanly to the current PR diff.
- Dependency updates are best-effort. The agent uses manifest-aware commands where available, then asks Nova to inspect remaining alerts and generate recommendations.
- Conflict resolution is guarded. Simple text conflicts can be resolved by Nova only under configured file-count limits.
