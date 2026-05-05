# ============================================================================
# Eval v2 — fallback questions
# Real evaluation items have been replaced with generic placeholders for the
# public mirror; the Firestore-backed runtime overrides these anyway.
# ============================================================================

def _q(i):
    return {"id": f"q{i}", "text_ko": f"문항 {i} (placeholder).",
            "text_en": f"Placeholder evaluation item {i}."}

_PLACEHOLDER_5 = [_q(i) for i in range(1, 6)]

DEFAULT_QUESTIONS = {
    "position": {
        "roles": [
            {"name": "GS",        "label_ko": "GS",        "min_count": 1, "questions": _PLACEHOLDER_5},
            {"name": "KT",        "label_ko": "KT",        "min_count": 3, "questions": _PLACEHOLDER_5},
            {"name": "BranchHead","label_ko": "Branch Head","min_count": 1, "questions": _PLACEHOLDER_5},
            {"name": "Team Lead", "label_ko": "TL",        "min_count": 1, "questions": _PLACEHOLDER_5},
        ]
    }
}
