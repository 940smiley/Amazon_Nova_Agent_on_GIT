from __future__ import annotations

import os

import requests


class NovaClient:
    def __init__(self, model_id: str | None = None, region: str | None = None):
        self.api_key = os.getenv("NOVA_API_KEY") or os.getenv("AMAZON_NOVA_API_KEY")
        if not self.api_key:
            raise RuntimeError("NOVA_API_KEY or AMAZON_NOVA_API_KEY is required")

        self.base_url = os.getenv(
            "NOVA_BASE_URL", "https://api.nova.amazon.com/v1"
        ).rstrip("/")
        self.model_id = model_id or os.getenv("NOVA_MODEL_ID", "amazon.nova-lite-v1")

    def chat(self, prompt: str) -> str:
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model_id,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are Nova, a senior codebase maintenance agent. "
                        "Return concise, actionable repository improvement suggestions."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
        }

        r = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=90,
        )

        if r.status_code >= 400:
            raise RuntimeError(f"Nova API failed: {r.status_code} {r.text[:1000]}")

        data = r.json()
        return data["choices"][0]["message"]["content"]

    def repo_suggestions(self, repo_name: str, inventory: str) -> str:
        return self.chat(f"""
Review this GitHub repository inventory and produce specific maintenance suggestions.

Repository:
{repo_name}

Inventory:
{inventory}

Focus on:
- dependency/security remediation
- stale PR/issue cleanup
- CI/CD improvements
- codebase modernization
- reliability improvements
- repo-specific next actions

Return markdown bullets.
""".strip())

