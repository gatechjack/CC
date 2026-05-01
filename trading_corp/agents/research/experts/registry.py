"""Expert registry — `(product_type, asset_class)` → list[role].

See planning/research_firm_design.md §5.4.

Flat dict for v3 (revisit grouped form when registry exceeds ~30 entries).
The engagement graph reads the registry at build/init time; cost
prediction = sum of cost-per-call per registered role. Adding a
(product, asset) pair = one row; adding a role = the new module + the
relevant rows here.
"""
from __future__ import annotations


EXPERT_REGISTRY: dict[tuple[str, str], list[str]] = {
    ("candidate_recommendation", "equity"):      ["technical", "fundamental", "macro", "sentiment"],
    ("candidate_recommendation", "option"):      ["technical", "fundamental", "macro", "sentiment"],
    ("candidate_recommendation", "crypto_spot"): ["technical", "macro", "sentiment"],
    ("trade_confirmation",       "equity"):      ["technical", "fundamental", "macro"],
    ("trade_confirmation",       "option"):      ["technical", "fundamental", "macro"],
    ("trade_confirmation",       "crypto_spot"): ["technical", "macro", "sentiment"],
    ("position_context",         "equity"):      ["macro", "sentiment"],
    ("position_context",         "option"):      ["macro", "sentiment"],
    ("position_context",         "crypto_spot"): ["macro", "sentiment"],
    ("thesis",                   "equity"):      ["technical", "fundamental", "macro", "sentiment"],
    ("thesis",                   "option"):      ["technical", "fundamental", "macro", "sentiment"],
    ("thesis",                   "crypto_spot"): ["technical", "macro", "sentiment"],
}


def experts_for(product_type: str, asset_class: str) -> list[str]:
    key = (product_type, asset_class)
    if key not in EXPERT_REGISTRY:
        raise KeyError(
            f"No expert set registered for {key!r}; "
            f"add a row to EXPERT_REGISTRY"
        )
    return list(EXPERT_REGISTRY[key])
