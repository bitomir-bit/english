"""Load flashcards and persist SRS state via Google Sheets."""
import os, json, base64
from googleapiclient.discovery import build
from google.oauth2.service_account import Credentials

SHEET_ID   = os.environ.get("SHEET_ID", "1W1ytTATU8shA_f9iHE_rSOR4ySNA2GlvnMC2u6-o2zI")
SHEET_NAME = "Flashcards"
_SCOPES    = ["https://www.googleapis.com/auth/spreadsheets"]

_LOCAL_CREDS = os.path.join(os.path.dirname(__file__),
               "../personality/ib_update/service_account.json")

def _service():
    b64 = os.environ.get("GOOGLE_CREDENTIALS_B64", "")
    if b64:
        info  = json.loads(base64.b64decode(b64).decode())
        creds = Credentials.from_service_account_info(info, scopes=_SCOPES)
    else:
        creds = Credentials.from_service_account_file(_LOCAL_CREDS, scopes=_SCOPES)
    return build("sheets", "v4", credentials=creds)

# ── Cards ──────────────────────────────────────────────────────────

def load_all():
    """Single API call — returns (cards, srs_state, id_row_map)."""
    result = _service().spreadsheets().values().get(
        spreadsheetId=SHEET_ID,
        range=f"{SHEET_NAME}!A:N"
    ).execute()
    rows = result.get("values", [])
    if not rows:
        return [], {}, {}

    cards       = []
    srs_state   = {}
    id_row_map  = {}

    for i, row in enumerate(rows[1:], start=2):
        row = list(row) + [""] * (14 - len(row))
        cid    = row[0].strip()
        status = row[9].strip()

        if cid.isdigit():
            id_row_map[cid] = i

        if "flagged" in status.lower() or not row[3].strip():
            continue

        cards.append({
            "id":      cid,
            "phrase":  row[1].strip(),
            "subtype": row[2].strip(),
            "front":   row[3].strip(),
            "back":    row[4].strip(),
            "blank":   row[5].strip(),
            "answer":  row[6].strip(),
            "section": row[7].strip(),
        })

        reps = row[10].strip()
        if reps and cid.isdigit():
            try:
                srs_state[cid] = {
                    "reps":     int(reps),
                    "interval": int(row[11]) if row[11].strip() else 1,
                    "due":      row[12].strip(),
                    "ease":     float(row[13]) if row[13].strip() else 2.5,
                }
            except ValueError:
                pass

    return cards, srs_state, id_row_map

# Keep these for backwards compatibility
def load_cards(status_filter=None):
    cards, _, _ = load_all()
    return cards

def load_srs_state():
    _, srs_state, _ = load_all()
    return srs_state

def build_id_row_map():
    _, _, id_row_map = load_all()
    return id_row_map

def save_card_state(card_id: str, card_state: dict, id_row_map: dict):
    """Write one card's SRS state to K-N immediately after rating."""
    sheet_row = id_row_map.get(str(card_id))
    if not sheet_row:
        return
    _service().spreadsheets().values().update(
        spreadsheetId=SHEET_ID,
        range=f"{SHEET_NAME}!K{sheet_row}:N{sheet_row}",
        valueInputOption="RAW",
        body={"values": [[
            card_state.get("reps", 0),
            card_state.get("interval", 1),
            card_state.get("due", ""),
            round(card_state.get("ease", 2.5), 2),
        ]]}
    ).execute()

def sync_scores(srs_state: dict):
    """Bulk-write all SRS state to sheet. Called at session end."""
    svc    = _service()
    result = svc.spreadsheets().values().get(
        spreadsheetId=SHEET_ID, range=f"{SHEET_NAME}!A:A"
    ).execute()
    id_rows = result.get("values", [])

    updates = []
    for i, row in enumerate(id_rows):
        if not row or not row[0].isdigit():
            continue
        cid = row[0]
        if cid not in srs_state:
            continue
        s = srs_state[cid]
        updates.append({
            "range": f"{SHEET_NAME}!K{i+1}:N{i+1}",
            "values": [[s.get("reps", 0), s.get("interval", 1),
                        s.get("due", ""), round(s.get("ease", 2.5), 2)]]
        })
    if updates:
        svc.spreadsheets().values().batchUpdate(
            spreadsheetId=SHEET_ID,
            body={"valueInputOption": "RAW", "data": updates}
        ).execute()

# ── Other ──────────────────────────────────────────────────────────

def flag_card(card_id: str):
    svc    = _service()
    result = svc.spreadsheets().values().get(
        spreadsheetId=SHEET_ID, range=f"{SHEET_NAME}!A:A"
    ).execute()
    rows   = result.get("values", [])
    for i, row in enumerate(rows):
        if row and row[0] == str(card_id):
            svc.spreadsheets().values().update(
                spreadsheetId=SHEET_ID,
                range=f"{SHEET_NAME}!J{i+1}",
                valueInputOption="RAW",
                body={"values": [["Flagged 🐛"]]}
            ).execute()
            return True
    return False

def append_card(phrase, subtype, front, back, blank, answer, section, source="Telegram bot"):
    svc    = _service()
    result = svc.spreadsheets().values().get(
        spreadsheetId=SHEET_ID, range=f"{SHEET_NAME}!A:A"
    ).execute()
    ids     = [r[0] for r in result.get("values", []) if r and r[0].isdigit()]
    next_id = max(int(i) for i in ids) + 1 if ids else 0
    row     = [str(next_id), phrase, subtype, front, back, blank, answer, section, source, "Review"]
    svc.spreadsheets().values().append(
        spreadsheetId=SHEET_ID,
        range=f"{SHEET_NAME}!A1",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": [row]}
    ).execute()
    return next_id
