"""Token budgeting. Port of wrapper/src/core/budget.rs (S00051).

v1 heuristic: chars / 3.5 (glyph-heavy Aether is denser than English).
Upgrade path (Known Limitation #4): the `tokenizers` package behind the same
interface — see Tasks For Codex, C-6.
"""


def est_tokens(text: str) -> int:
    return round(len(text) / 3.5)


def over_budget(est: int, context_window: int, max_fraction: float) -> bool:
    """True if the estimate exceeds max_fraction of the context window."""
    return est > context_window * max_fraction


def dynamic_room(context_window: int, briefcase_est: int, packet_est: int, tool_schema_est: int) -> int:
    """Tokens left after fixed dispatch overhead is loaded."""
    return context_window - (briefcase_est + packet_est + tool_schema_est)


def floor_ok(room: int, floor: int) -> bool:
    """True when dynamic room meets the absolute floor."""
    return room >= floor
