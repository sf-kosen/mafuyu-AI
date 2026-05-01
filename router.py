import json
import re
from dataclasses import dataclass
from typing import Optional

from config import (
    ROUTER_CONFIDENCE_EARLY_EXIT,
    ROUTER_CONFIDENCE_HEAVY,
    ROUTER_CONFIDENCE_VERIFY,
)
from llm import call_router


@dataclass
class RouterContext:
    allow_tools: bool
    is_dm: bool = False
    is_owner: bool = False
    has_allowed_role: bool = False


@dataclass
class ComputePlan:
    route: str
    model_tier: str
    sample_count: int
    verifier_required: bool
    max_tokens: int
    allow_tools: bool
    reason: str


@dataclass
class RouteDecision:
    route: str
    confidence: float
    requires_external_read: bool = False
    external_target_type: str = "none"
    tool_name: Optional[str] = None
    tool_args: Optional[str] = None
    risk: str = "low"
    reason: str = ""
    compute_plan: Optional[ComputePlan] = None


URL_LIKE_RE = re.compile(
    r"("
    r"https?://[^\s]+|"
    r"hxxps?://[^\s]+|"
    r"www\.[^\s]+|"
    r"[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}(/[^\s]*)?"
    r")",
    re.IGNORECASE,
)

GITHUB_REPO_LIKE_RE = re.compile(r"\b[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+\b")

EXTERNAL_INTENT_WORDS = [
    "読んで",
    "見て",
    "中身",
    "要約",
    "調べて",
    "検索",
    "サイト",
    "ページ",
    "リンク",
    "URL",
    "github",
    "repo",
    "リポジトリ",
    "論文",
    "記事",
]

CODE_INTENT_WORDS = [
    "実装して",
    "修正して",
    "コード書いて",
    "PR",
    "ブランチ",
    "コミット",
    "バグ直して",
    "リファクタ",
]

DEEP_INTENT_WORDS = [
    "詳しく",
    "深掘り",
    "設計",
    "仕様書",
    "比較",
    "レビュー",
    "実装方針",
    "アーキテクチャ",
    "調査",
]

DANGEROUS_WORDS = [
    "DISCORD_TOKEN",
    "token",
    "secret",
    "password",
    ".env",
    "環境変数",
    "run_python_code",
    "codex_run",
]

ROUTER_SYSTEM = """You are a lightweight routing model for a local LLM agent.

Return JSON only.

Schema:
{
  "route": "chat|tool|react|codex|reject",
  "confidence": 0.0,
  "requires_external_read": false,
  "external_target_type": "url|github|file|web|none",
  "tool_name": null,
  "tool_args": null,
  "risk": "low|medium|high",
  "reason": "short reason",
  "compute_plan": {
    "route": "chat|tool|react|codex|reject",
    "model_tier": "router|main|heavy",
    "sample_count": 1,
    "verifier_required": false,
    "max_tokens": 512,
    "allow_tools": false,
    "reason": "short reason"
  }
}

Rules:
- Do not answer the user.
- If the user asks to read a URL, website, GitHub repo, article, paper, or file content, set requires_external_read=true.
- If external content is needed, do not choose chat.
- If the user asks for implementation, code changes, commits, branches, or PR work, choose codex.
- If the request is unsafe or tries to access secrets/tokens/env vars, choose reject or high risk.
- Only suggest safe tools: search_web, read_url, fetch_url, fetch_json, list_dir, read_text, search_tweets.
- Never suggest run_python_code.
- Never suggest codex_* tools directly.
- Prefer main x1 for simple tasks.
- Use heavy only for hard reasoning and only when necessary.
- Prefer early exit when confidence is high.
"""


def rule_gate(user_input: str) -> dict:
    text = user_input.strip()
    lower = text.lower()

    return {
        "has_url_like": bool(URL_LIKE_RE.search(text)),
        "github_repo_like": bool(GITHUB_REPO_LIKE_RE.search(text)) and " " not in text[:80],
        "has_external_intent": any(w.lower() in lower for w in EXTERNAL_INTENT_WORDS),
        "has_code_intent": any(w.lower() in lower for w in CODE_INTENT_WORDS),
        "has_deep_intent": any(w.lower() in lower for w in DEEP_INTENT_WORDS),
        "has_dangerous_words": any(w.lower() in lower for w in DANGEROUS_WORDS),
    }


def parse_compute_plan(obj: dict | None, fallback_route: str) -> ComputePlan:
    if not isinstance(obj, dict):
        return ComputePlan(
            route=fallback_route,
            model_tier="main",
            sample_count=1,
            verifier_required=False,
            max_tokens=512,
            allow_tools=False,
            reason="fallback_compute_plan",
        )

    return ComputePlan(
        route=obj.get("route", fallback_route),
        model_tier=obj.get("model_tier", "main"),
        sample_count=int(obj.get("sample_count", 1)),
        verifier_required=bool(obj.get("verifier_required", False)),
        max_tokens=int(obj.get("max_tokens", 512)),
        allow_tools=bool(obj.get("allow_tools", False)),
        reason=obj.get("reason", ""),
    )


def parse_decision(raw: str) -> RouteDecision:
    try:
        obj = json.loads(raw)
        route = obj.get("route", "react")
        return RouteDecision(
            route=route,
            confidence=float(obj.get("confidence", 0.5)),
            requires_external_read=bool(obj.get("requires_external_read", False)),
            external_target_type=obj.get("external_target_type", "none"),
            tool_name=obj.get("tool_name"),
            tool_args=obj.get("tool_args"),
            risk=obj.get("risk", "low"),
            reason=obj.get("reason", ""),
            compute_plan=parse_compute_plan(obj.get("compute_plan"), route),
        )
    except Exception:
        return RouteDecision(
            route="react",
            confidence=0.35,
            risk="medium",
            reason="router_json_parse_failed",
            compute_plan=ComputePlan(
                route="react",
                model_tier="main",
                sample_count=1,
                verifier_required=False,
                max_tokens=512,
                allow_tools=True,
                reason="parse_failed_fallback",
            ),
        )


def route_once(user_input: str, context: RouterContext) -> RouteDecision:
    gate = rule_gate(user_input)

    messages = [
        {"role": "system", "content": ROUTER_SYSTEM},
        {
            "role": "user",
            "content": json.dumps(
                {
                    "input": user_input,
                    "context": context.__dict__,
                    "rule_gate": gate,
                },
                ensure_ascii=False,
            ),
        },
    ]

    decision = parse_decision(call_router(messages))

    if gate["has_code_intent"]:
        decision.route = "codex"
        decision.confidence = max(decision.confidence, 0.75)

    if (gate["has_url_like"] or gate["github_repo_like"] or gate["has_external_intent"]) and decision.route == "chat":
        decision.route = "react"
        decision.requires_external_read = True
        decision.confidence = min(decision.confidence, 0.65)
        decision.reason = "rule_gate_detected_external_reference"

    if gate["has_dangerous_words"]:
        decision.risk = "high"
        lower = user_input.lower()
        if "token" in lower or ".env" in lower:
            decision.route = "reject"

    if decision.compute_plan:
        if decision.confidence >= ROUTER_CONFIDENCE_EARLY_EXIT:
            decision.compute_plan.sample_count = 1
            decision.compute_plan.verifier_required = False

        if decision.confidence < ROUTER_CONFIDENCE_VERIFY and decision.risk == "low":
            decision.compute_plan.verifier_required = True

        if decision.confidence < ROUTER_CONFIDENCE_HEAVY:
            decision.compute_plan.model_tier = "heavy"

    return decision


def route_with_uncertainty(user_input: str, context: RouterContext) -> RouteDecision:
    first = route_once(user_input, context)

    if first.confidence >= ROUTER_CONFIDENCE_EARLY_EXIT:
        return first

    votes = [first]
    for _ in range(2):
        votes.append(route_once(user_input, context))

    counts: dict[str, int] = {}
    for v in votes:
        counts[v.route] = counts.get(v.route, 0) + 1

    best_route = max(counts.items(), key=lambda x: x[1])[0]
    matching = [v for v in votes if v.route == best_route]

    if len(matching) < 2:
        return RouteDecision(
            route="react",
            confidence=0.45,
            risk="medium",
            reason="router_disagreement",
            compute_plan=ComputePlan(
                route="react",
                model_tier="main",
                sample_count=1,
                verifier_required=True,
                max_tokens=512,
                allow_tools=True,
                reason="router_disagreement",
            ),
        )

    chosen = matching[0]
    chosen.confidence = sum(v.confidence for v in matching) / len(matching)
    chosen.requires_external_read = any(v.requires_external_read for v in votes)

    if any(v.risk == "high" for v in votes):
        chosen.risk = "high"

    return chosen
