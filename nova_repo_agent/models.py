from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone


@dataclass
class Finding:
    timestamp: str
    owner: str
    repo: str
    kind: str
    severity: str
    title: str
    action: str = "observed"
    state: str = ""
    url: str = ""
    details: str = ""

    @classmethod
    def now(
        cls,
        owner,
        repo,
        kind,
        severity,
        title,
        *,
        action="observed",
        state="",
        url="",
        details="",
    ):
        return cls(
            datetime.now(timezone.utc).isoformat(),
            owner,
            repo,
            kind,
            severity or "",
            title or "",
            action or "",
            state or "",
            url or "",
            details or "",
        )

    def to_dict(self):
        return asdict(self)

