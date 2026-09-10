from __future__ import annotations


def score_event(event: dict) -> tuple[int, list[str]]:
    """Heuristic 0-100 signal score. Intentionally transparent and editable."""
    form = (event.get("form") or "").upper()
    event_type = event.get("event_type") or ""
    tx_code = (event.get("transaction_code") or "").upper()
    value = float(event.get("value") or 0)
    role = (event.get("role") or "").lower()

    score = 25
    reasons: list[str] = []

    if form in {"SC 13D", "SC 13D/A"}:
        score += 38
        reasons.append("activist/significant ownership filing")
    elif form in {"SC 13G", "SC 13G/A"}:
        score += 20
        reasons.append("large beneficial ownership filing")
    elif form == "144":
        score += 18
        reasons.append("proposed affiliate sale")
    elif form == "8-K":
        score += 12
        reasons.append("material corporate event")

    if event_type == "insider_transaction":
        if tx_code == "P":
            score += 32
            reasons.append("open-market insider purchase")
        elif tx_code == "S":
            score += 12
            reasons.append("open-market insider sale")
        elif tx_code in {"A", "M", "F", "G"}:
            score -= 12
            reasons.append("lower-signal award/exercise/tax/gift transaction")

        if any(x in role for x in ("chief executive", "ceo", "chief financial", "cfo")):
            score += 9
            reasons.append("senior executive transaction")
        elif "director" in role:
            score += 4
            reasons.append("director transaction")

        if value >= 1_000_000:
            score += 15
            reasons.append("transaction value >= $1M")
        elif value >= 500_000:
            score += 10
            reasons.append("transaction value >= $500K")
        elif value >= 100_000:
            score += 5
            reasons.append("transaction value >= $100K")

    score = max(0, min(100, score))
    return score, reasons


def band(score: int) -> str:
    if score >= 80:
        return "High"
    if score >= 60:
        return "Interesting"
    if score >= 40:
        return "Notable"
    return "Routine"
