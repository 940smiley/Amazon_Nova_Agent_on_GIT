from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Dict, Optional


@dataclass
class Finding:
    timestamp: str
    owner: str
    repo: str
    category: str
    severity: str
    title: str
    url: str = ""
    state: str = ""
    action: str = "observed"
    details: str = ""

    @staticmethod
    def now(owner: str, repo: str, category: str, severity: str, title: str, **kwargs: Any) -> "Finding":
        return Finding(
            timestamp=datetime.now(timezone.utc).isoformat(),
            owner=owner,
            repo=repo,
            category=category,
            severity=severity,
            title=title,
            **kwargs,
        )

    def row(self) -> Dict[str, str]:
        return {k: str(v) for k, v in asdict(self).items()}
