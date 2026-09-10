"""Native Claude defaults shared by Bob's subprocess callers."""

FABLE_MODEL = "claude-fable-5-1[1m]"


def resolve_claude_model(model: str | None) -> str:
    """Pin the default and Fable alias; preserve explicit model choices."""
    if model in (None, "", "fable", "fable[1m]"):
        return FABLE_MODEL
    return model
