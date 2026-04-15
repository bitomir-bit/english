"""SM-2 spaced repetition engine.
State is stored in Google Sheet (columns K-N) for cloud persistence.
Local JSON file is used as a fast cache and fallback.
"""
import json, os
from datetime import date, timedelta

_data_dir  = os.environ.get("DATA_DIR", os.path.dirname(__file__))
STATE_PATH = os.path.join(_data_dir, "srs_state.json")

RATING_MAP = {
    "again": (1,   -0.2),
    "hard":  (3,   -0.1),
    "good":  (None, 0.0),
    "easy":  (None, 0.1),
}
MIN_EASE     = 1.3
INIT_EASE    = 2.5
NEW_INTERVAL = {"again": 1, "hard": 3, "good": 4, "easy": 7}

# ── In-memory cache (loaded once at startup) ───────────────────────
_state_cache = None

def _load():
    global _state_cache
    if _state_cache is not None:
        return _state_cache
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            _state_cache = json.load(f)
    else:
        _state_cache = {}
    return _state_cache

def _save(state: dict):
    global _state_cache
    _state_cache = state
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)

def load_from_sheet(sheet_state: dict):
    """Merge sheet state into local cache (sheet wins for reviewed cards)."""
    global _state_cache
    local = _load().copy()
    local.update(sheet_state)  # sheet is source of truth
    _state_cache = local
    _save(local)

# ── SM-2 ───────────────────────────────────────────────────────────

def _next_interval(card_state, rating):
    reps     = card_state.get("reps", 0)
    interval = card_state.get("interval", 1)
    ease     = card_state.get("ease", INIT_EASE)

    if rating == "again":
        return 1

    ease_adj = RATING_MAP[rating][1]
    ease     = max(MIN_EASE, ease + ease_adj)

    if reps == 0:
        new_interval = NEW_INTERVAL[rating]
    elif reps == 1:
        new_interval = NEW_INTERVAL[rating] * 2
    else:
        if rating == "hard":
            new_interval = max(3, round(interval * 1.2))
        elif rating == "easy":
            new_interval = round(interval * ease * 1.3)
        else:
            new_interval = round(interval * ease)

    return max(1, new_interval)

def rate_card(card_id: str, rating: str):
    """Update card state and save. Returns new state dict."""
    state = _load()
    card  = state.get(card_id, {"reps": 0, "interval": 1, "ease": INIT_EASE, "due": str(date.today())})

    new_interval = _next_interval(card, rating)
    new_ease     = max(MIN_EASE, card.get("ease", INIT_EASE) + RATING_MAP[rating][1])
    new_reps     = 0 if rating == "again" else card.get("reps", 0) + 1
    new_due      = str(date.today() + timedelta(days=new_interval))

    card.update({"reps": new_reps, "interval": new_interval,
                 "ease": round(new_ease, 3), "due": new_due})
    state[card_id] = card
    _save(state)
    return card

def get_due_cards(all_cards: list):
    """Return cards due today or overdue (oldest first), then new cards."""
    state = _load()
    today = str(date.today())
    due, new = [], []

    for card in all_cards:
        cid = card["id"]
        if cid not in state:
            new.append(card)
        elif state[cid]["due"] <= today:
            due.append((state[cid]["due"], card))

    due.sort(key=lambda x: x[0])
    return [c for _, c in due] + new

def get_stats(all_cards: list):
    state    = _load()
    seen_ids = set(state.keys())
    reviewed = [c for c in all_cards if c["id"] in seen_ids]
    weak     = [c for c in reviewed
                if state[c["id"]].get("interval", 1) <= 1
                and state[c["id"]].get("reps", 0) == 0]
    due      = len(get_due_cards(all_cards))
    meta     = state.get("__meta__", {})

    return {
        "streak":     meta.get("streak", 0),
        "due":        due,
        "total":      len(all_cards),
        "reviewed":   len(reviewed),
        "weak":       len(weak),
        "weak_cards": weak[:10],
    }

def record_daily_review():
    state     = _load()
    meta      = state.get("__meta__", {"streak": 0, "last_review": ""})
    today     = str(date.today())
    yesterday = str(date.today() - timedelta(days=1))

    if meta.get("last_review") == yesterday:
        meta["streak"] = meta.get("streak", 0) + 1
    elif meta.get("last_review") != today:
        meta["streak"] = 1

    meta["last_review"] = today
    state["__meta__"]   = meta
    _save(state)
