from __future__ import annotations
import json
import os
from typing import Any, Dict
import boto3


class NovaClient:
    def __init__(self, model_id: str, region: str) -> None:
        self.model_id = model_id
        self.client = boto3.client("bedrock-runtime", region_name=region)

    def text(self, prompt: str, max_tokens: int = 2500) -> str:
        resp = self.client.converse(
            modelId=self.model_id,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": max_tokens, "temperature": 0.2, "topP": 0.9},
        )
        blocks = resp.get("output", {}).get("message", {}).get("content", [])
        return "\n".join(block.get("text", "") for block in blocks if "text" in block).strip()

    def repo_suggestions(self, repo_name: str, inventory: str) -> str:
        return self.text(f"""
You are auditing GitHub repository {repo_name}. Produce a concise prioritized improvement plan.
Focus on security, dependency hygiene, CI reliability, tests, DX, docs, packaging, deployment, and maintainability.
Do not invent facts. Base recommendations only on this inventory/log:

{inventory}
""", max_tokens=3000)

    def resolve_conflict(self, file_path: str, conflicted_text: str, context: str) -> str:
        return self.text(f"""
Resolve this git conflict for file `{file_path}`. Return ONLY the final file content, no markdown fences.
Preserve intended behavior from both sides when possible. If uncertain, choose the safer minimal merge.

Context:
{context}

Conflicted file:
{conflicted_text}
""", max_tokens=6000)
