from dataclasses import dataclass


@dataclass
class InferenceBudget:
    max_model_calls: int
    max_total_tokens: int
    allow_heavy: bool
    allow_best_of_n: bool
    allow_react: bool


DEFAULT_BUDGET = InferenceBudget(
    max_model_calls=2,
    max_total_tokens=1200,
    allow_heavy=False,
    allow_best_of_n=False,
    allow_react=True,
)

DEEP_BUDGET = InferenceBudget(
    max_model_calls=4,
    max_total_tokens=3000,
    allow_heavy=True,
    allow_best_of_n=True,
    allow_react=True,
)

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


def select_budget(user_input: str) -> InferenceBudget:
    lower = user_input.lower()
    if any(w.lower() in lower for w in DEEP_INTENT_WORDS):
        return DEEP_BUDGET
    return DEFAULT_BUDGET
