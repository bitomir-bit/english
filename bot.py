#!/usr/bin/env python3
"""
Telegram Flashcard Bot — Full build
Commands: /start, /study, /stats, /add, /reload
"""
import json, time, logging, os, threading, urllib.request, urllib.parse
from datetime import date, datetime
import sheets, srs, claude_api

TOKEN   = os.environ.get("TELEGRAM_TOKEN", "8668507518:AAEOlluogIQvX011YrgkDhj_WPrqoXXxPxg")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "258041181")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

MAX_NEW_PER_DAY = 10

# ── Global state ───────────────────────────────────────────────────
all_cards      = []   # loaded at startup, appended when new cards added
session        = {}   # study session per chat_id
add_word_state = {}   # add-word flow per chat_id
id_row_map     = {}   # {card_id: sheet_row} for fast SRS writes

# ── Telegram API ───────────────────────────────────────────────────

def _api(method: str, **kwargs) -> dict:
    url  = f"https://api.telegram.org/bot{TOKEN}/{method}"
    data = json.dumps(kwargs).encode()
    req  = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)

def send(chat_id, text, reply_markup=None, parse_mode="HTML"):
    params = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
    if reply_markup:
        params["reply_markup"] = reply_markup
    return _api("sendMessage", **params)

def edit(chat_id, message_id, text, reply_markup=None, parse_mode="HTML"):
    params = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": parse_mode}
    if reply_markup:
        params["reply_markup"] = reply_markup
    try:
        return _api("editMessageText", **params)
    except Exception:
        return None

def answer_callback(callback_id, text=""):
    try:
        _api("answerCallbackQuery", callback_query_id=callback_id, text=text)
    except Exception:
        pass

def inline_kbd(rows):
    return {"inline_keyboard": [
        [{"text": lbl, "callback_data": cd} for lbl, cd in row]
        for row in rows
    ]}

# ── Card rendering ─────────────────────────────────────────────────

SUBTYPE_EMOJI = {
    "Vocabulary":          "📗",
    "Phrasal Verb":        "🔀",
    "Idiom":               "💡",
    "Collocation":         "🔗",
    "Business English":    "💼",
    "Everyday Phrase":     "💬",
    "Common Correction":   "✅",
}

def card_emoji(card):
    return SUBTYPE_EMOJI.get(card.get("subtype", ""), "📖")

def use_blank(card):
    return bool(card.get("blank") and card.get("answer"))

def front_text(card, progress=""):
    emoji   = card_emoji(card)
    subtype = card.get("subtype", "")
    suffix  = f"\n\n<i>{progress}</i>" if progress else ""
    if use_blank(card):
        definition = card["back"].split("→")[0].strip().rstrip(".")
        return (f"<b>{emoji} {subtype}</b>\n\n"
                f"<i>{definition}.</i>\n\n"
                f"Complete: {card['blank']}{suffix}")
    return f"<b>{emoji} {subtype}</b>\n\n{card['front']}{suffix}"

def back_text(card, progress=""):
    emoji   = card_emoji(card)
    subtype = card.get("subtype", "")
    suffix  = f"\n\n<i>{progress}</i>" if progress else ""
    lines   = [f"<b>{emoji} {subtype}</b>"]
    if use_blank(card):
        ans    = card["answer"]
        filled = card["blank"].replace("___", f"<b>{ans}</b>")
        lines.append(f"\n✅ <b>{ans}</b>")
        lines.append(f"<i>{filled}</i>")
        lines.append(f"\n{card['back']}")
    else:
        lines.append(f"\n{card['back']}")
    return "\n".join(lines) + suffix

def rating_kbd(card_id):
    return inline_kbd([
        [("😰 Again", "rate:again"), ("😓 Hard", "rate:hard"),
         ("🙂 Good",  "rate:good"),  ("😄 Easy", "rate:easy")],
        [("🐛 Flag this card", f"flag:{card_id}")],
    ])

def flip_kbd():
    return inline_kbd([[("👁 Reveal answer", "flip")]])

# ── Menu & stats ───────────────────────────────────────────────────

def send_menu(chat_id):
    stats  = srs.get_stats(all_cards)
    today  = date.today().strftime("%A · %B %-d")
    due    = stats["due"]
    streak = stats["streak"]
    weak   = stats["weak"]
    text = (f"Hey Mykhailo 👋\n<i>{today}</i>\n\n"
            f"🔥 <b>{streak}</b> day streak   "
            f"📚 <b>{due}</b> due   "
            f"⚠️ <b>{weak}</b> weak")
    kbd = inline_kbd([
        [(f"📚 Study ({due} due)", "study")],
        [("➕ Add word",    "add_word"), ("📊 Stats",      "stats")],
        [("⚠️ Weak words", "weak"),     ("🔄 Reload deck", "reload")],
    ])
    send(chat_id, text, reply_markup=kbd)

def send_stats(chat_id):
    stats = srs.get_stats(all_cards)
    s     = srs._load()
    # Retention: ratio of good/easy reps among reviewed cards
    total_reps   = sum(c.get("reps", 0) for cid, c in s.items() if cid != "__meta__")
    reviewed_cnt = sum(1 for cid, c in s.items() if cid != "__meta__" and c.get("reps", 0) > 0)
    retention    = round(100 * reviewed_cnt / max(len(all_cards), 1))
    text = (f"<b>📊 Your stats</b>\n\n"
            f"🔥 Streak: <b>{stats['streak']} days</b>\n"
            f"📚 Due today: <b>{stats['due']}</b>\n"
            f"✅ Seen: <b>{stats['reviewed']}</b> / {stats['total']} cards\n"
            f"📈 Deck coverage: <b>{retention}%</b>\n"
            f"⚠️ Weak words: <b>{stats['weak']}</b>\n"
            f"🎯 Total reviews ever: <b>{total_reps}</b>")
    send(chat_id, text, reply_markup=inline_kbd([[("🏠 Main menu", "menu")]]))

def send_weak(chat_id):
    stats = srs.get_stats(all_cards)
    weak  = stats["weak_cards"]
    if not weak:
        send(chat_id, "No weak words yet — keep studying! 💪",
             reply_markup=inline_kbd([[("🏠 Main menu", "menu")]]))
        return
    lines = ["<b>⚠️ Weak words</b> — cards you keep forgetting\n"]
    for c in weak:
        back = c["back"][:55] + "…" if len(c["back"]) > 55 else c["back"]
        lines.append(f"• <b>{c['phrase']}</b> — {back}")
    send(chat_id, "\n".join(lines),
         reply_markup=inline_kbd([
             [("📚 Study weak words", "study_weak")],
             [("🏠 Main menu", "menu")],
         ]))

# ── Study session ──────────────────────────────────────────────────

def start_study(chat_id, extended=False, weak_only=False):
    if weak_only:
        s     = srs._load()
        queue = [c for c in all_cards
                 if c["id"] in s and s[c["id"]].get("interval", 1) <= 1
                 and s[c["id"]].get("reps", 0) == 0]
        if not queue:
            send(chat_id, "No weak words to study right now 🎉",
                 reply_markup=inline_kbd([[("🏠 Main menu", "menu")]]))
            return
    else:
        queue = srs.get_due_cards(all_cards)
        if not queue and not extended:
            send(chat_id, "🎉 All caught up! No cards due right now.",
                 reply_markup=inline_kbd([
                     [("▶️ Keep going (new cards)", "study_extended")],
                     [("🏠 Main menu", "menu")],
                 ]))
            return
        if extended and not queue:
            send(chat_id,
                 "🎉 You've seen every card! All reviews scheduled for future dates.\n\nCome back tomorrow 💪",
                 reply_markup=inline_kbd([[("🏠 Main menu", "menu")]]))
            return

    session[chat_id] = {
        "queue":     queue,
        "done":      0,
        "new_today": session.get(chat_id, {}).get("new_today", 0),
        "extended":  extended,
    }
    srs.record_daily_review()
    show_next_card(chat_id)

def show_next_card(chat_id):
    sess = session.get(chat_id)
    if not sess or not sess["queue"]:
        finish_session(chat_id)
        return

    queue = sess["queue"]

    if not sess.get("extended"):
        state    = srs._load()
        filtered = [c for c in queue
                    if c["id"] in state or sess["new_today"] < MAX_NEW_PER_DAY]
        if not filtered:
            finish_session(chat_id)
            return
        queue = filtered

    card             = queue[0]
    sess["queue"]    = queue[1:]
    sess["current"]  = card
    sess["showing_back"] = False

    total    = sess["done"] + 1 + len(sess["queue"])
    progress = f"Card {sess['done'] + 1} / {total}"
    text     = front_text(card, progress)
    resp     = send(chat_id, text, reply_markup=flip_kbd())
    sess["card_msg_id"] = resp["result"]["message_id"]

def finish_session(chat_id):
    sess = session.pop(chat_id, {})
    done = sess.get("done", 0)
    try:
        sheets.sync_scores(srs._load())
    except Exception as e:
        log.warning(f"Score sync failed: {e}")
    send(chat_id,
         f"✅ <b>Session done!</b>\n\nReviewed: <b>{done}</b> cards 💪",
         reply_markup=inline_kbd([
             [("▶️ Keep going", "study_extended")],
             [("🏠 Main menu", "menu")],
         ]))

# ── Add word flow ──────────────────────────────────────────────────

def start_add_word(chat_id):
    add_word_state[chat_id] = {"step": "awaiting_input"}
    resp = send(chat_id,
                "➕ <b>Add a new card</b>\n\nSend me the word or phrase:",
                reply_markup=inline_kbd([[("❌ Cancel", "cancel_add")]]))
    add_word_state[chat_id]["prompt_msg_id"] = resp["result"]["message_id"]

def handle_word_input(chat_id, phrase):
    state = add_word_state.get(chat_id, {})
    if state.get("step") != "awaiting_input":
        return False  # not in add-word flow

    # Show loading
    loading_resp = send(chat_id, f"⏳ Generating card for <b>{phrase}</b>…")
    loading_id   = loading_resp["result"]["message_id"]

    card = claude_api.generate_card(phrase)

    if not card:
        edit(chat_id, loading_id, "❌ Failed to generate card. Try again or check your ANTHROPIC_API_KEY.")
        add_word_state.pop(chat_id, None)
        return True

    add_word_state[chat_id] = {
        "step":       "previewing",
        "phrase":     phrase,
        "card":       card,
        "loading_id": loading_id,
    }

    # Delete loading message and show preview
    try:
        _api("deleteMessage", chat_id=chat_id, message_id=loading_id)
    except Exception:
        pass

    show_card_preview(chat_id)
    return True

def show_card_preview(chat_id):
    state = add_word_state.get(chat_id, {})
    card  = state.get("card", {})
    emoji = SUBTYPE_EMOJI.get(card.get("subtype", ""), "📖")

    blank_line = f"\n<b>Blank:</b> {card['blank']}" if card.get("blank") else ""
    text = (f"{emoji} <b>{card.get('subtype', '')}</b>\n\n"
            f"<b>Front:</b> {card.get('front', '')}\n\n"
            f"<b>Back:</b> {card.get('back', '')}"
            f"{blank_line}\n\n"
            f"<i>Looks good? Save it or cancel.</i>")
    send(chat_id, text, reply_markup=inline_kbd([
        [("✅ Save card", "confirm_card"), ("❌ Cancel", "cancel_add")],
    ]))

def confirm_add_word(chat_id):
    state  = add_word_state.pop(chat_id, {})
    card   = state.get("card", {})
    phrase = state.get("phrase", "")

    if not card:
        send(chat_id, "Something went wrong. Try again.",
             reply_markup=inline_kbd([[("🏠 Main menu", "menu")]]))
        return

    new_id = sheets.append_card(
        phrase  = phrase,
        subtype = card.get("subtype", "Vocabulary"),
        front   = card.get("front", ""),
        back    = card.get("back", ""),
        blank   = card.get("blank", ""),
        answer  = card.get("answer", ""),
        section = card.get("section", "Vocabulary (Single Words)"),
        source  = "Telegram bot",
    )

    # Add to in-memory deck immediately
    all_cards.append({
        "id":      str(new_id),
        "phrase":  phrase,
        "subtype": card.get("subtype", "Vocabulary"),
        "front":   card.get("front", ""),
        "back":    card.get("back", ""),
        "blank":   card.get("blank", ""),
        "answer":  card.get("answer", ""),
        "section": card.get("section", ""),
    })

    send(chat_id,
         f"✅ <b>{phrase}</b> added to your deck!\n"
         f"<i>Status: Review — edit in the sheet when ready.</i>\n"
         f"Total cards: <b>{len(all_cards)}</b>",
         reply_markup=inline_kbd([
             [("📚 Study now", "study"), ("➕ Add another", "add_word")],
             [("🏠 Main menu", "menu")],
         ]))

# ── Callback router ────────────────────────────────────────────────

def handle_callback(cq):
    chat_id = cq["message"]["chat"]["id"]
    msg_id  = cq["message"]["message_id"]
    data    = cq["data"]
    answer_callback(cq["id"])

    if data == "menu":
        send_menu(chat_id)
    elif data == "study":
        start_study(chat_id)
    elif data == "study_extended":
        start_study(chat_id, extended=True)
    elif data == "study_weak":
        start_study(chat_id, weak_only=True)
    elif data == "stats":
        send_stats(chat_id)
    elif data == "weak":
        send_weak(chat_id)
    elif data == "add_word":
        start_add_word(chat_id)
    elif data == "confirm_card":
        confirm_add_word(chat_id)
    elif data == "cancel_add":
        add_word_state.pop(chat_id, None)
        send(chat_id, "Cancelled.",
             reply_markup=inline_kbd([[("🏠 Main menu", "menu")]]))
    elif data == "reload":
        reload_cards(chat_id)

    elif data == "flip":
        sess = session.get(chat_id)
        if not sess or sess.get("showing_back"):
            return
        card = sess["current"]
        sess["showing_back"] = True
        total    = sess["done"] + 1 + len(sess["queue"])
        progress = f"Card {sess['done'] + 1} / {total}"
        edit(chat_id, msg_id, back_text(card, progress), reply_markup=rating_kbd(card["id"]))

    elif data.startswith("rate:"):
        rating = data.split(":")[1]
        sess   = session.get(chat_id)
        if not sess:
            return
        card     = sess["current"]
        state    = srs._load()
        is_new   = card["id"] not in state
        new_state = srs.rate_card(card["id"], rating)
        if is_new:
            sess["new_today"] = sess.get("new_today", 0) + 1
        # Persist to sheet immediately (survives cloud restarts)
        try:
            sheets.save_card_state(card["id"], new_state, id_row_map)
        except Exception as e:
            log.warning(f"Sheet write failed: {e}")
        sess["done"] += 1
        show_next_card(chat_id)

    elif data.startswith("flag:"):
        card_id = data.split(":")[1]
        sess    = session.get(chat_id, {})
        phrase  = sess.get("current", {}).get("phrase", card_id)
        ok = sheets.flag_card(card_id)
        if ok:
            send(chat_id, f"🐛 <b>{phrase}</b> flagged — check Status column in your sheet.")
        else:
            send(chat_id, "Couldn't flag that card.")

# ── Reload deck ────────────────────────────────────────────────────

def reload_cards(chat_id=None):
    global all_cards, id_row_map
    log.info("Reloading cards from sheet…")
    all_cards  = sheets.load_cards()
    id_row_map = sheets.build_id_row_map()
    log.info(f"Reloaded — {len(all_cards)} cards")
    if chat_id:
        send(chat_id, f"🔄 Deck reloaded — <b>{len(all_cards)}</b> cards ready.",
             reply_markup=inline_kbd([[("🏠 Main menu", "menu")]]))

# ── Message router ─────────────────────────────────────────────────

def handle_message(msg):
    chat_id = msg["chat"]["id"]
    text    = msg.get("text", "").strip()

    if text in ("/start", "/menu"):
        add_word_state.pop(chat_id, None)
        send_menu(chat_id)
    elif text == "/study":
        start_study(chat_id)
    elif text == "/stats":
        send_stats(chat_id)
    elif text == "/add":
        start_add_word(chat_id)
    elif text == "/reload":
        reload_cards(chat_id)
    elif text.startswith("/"):
        send(chat_id, "Unknown command. Use /start for the menu.")
    else:
        # Try add-word flow first
        if not handle_word_input(chat_id, text):
            send(chat_id, "Use /start to open the menu, or /add to add a new card.")

# ── Daily reminder ─────────────────────────────────────────────────

def _reminder_loop():
    """Send 6:30am reminder on weekdays (Mon-Fri). Runs in background thread."""
    sent_today = None
    while True:
        try:
            now = datetime.now()
            # Mon=0 … Fri=4
            if now.weekday() < 5 and now.hour == 6 and now.minute == 30:
                today_str = str(date.today())
                if sent_today != today_str:
                    sent_today = today_str
                    stats = srs.get_stats(all_cards)
                    due   = stats["due"]
                    streak = stats["streak"]
                    msg = (f"☀️ Good morning!\n\n"
                           f"🔥 Streak: <b>{streak} days</b>\n"
                           f"📚 Cards due today: <b>{due}</b>\n\n"
                           f"Start your session with /study 👇")
                    send(CHAT_ID, msg)
                    log.info("Morning reminder sent.")
        except Exception as e:
            log.warning(f"Reminder error: {e}")
        time.sleep(30)  # check every 30 seconds

# ── Main loop ──────────────────────────────────────────────────────

def run():
    global all_cards
    log.info("Loading cards from Google Sheets…")
    all_cards = sheets.load_cards()
    log.info(f"Loaded {len(all_cards)} cards")

    log.info("Loading SRS state from sheet…")
    sheet_state = sheets.load_srs_state()
    srs.load_from_sheet(sheet_state)
    log.info(f"SRS state: {len(sheet_state)} reviewed cards")

    log.info("Building ID→row map…")
    global id_row_map
    id_row_map = sheets.build_id_row_map()

    # Start daily reminder in background thread
    t = threading.Thread(target=_reminder_loop, daemon=True)
    t.start()
    log.info("Daily reminder scheduled — weekdays at 06:30")

    offset = 0
    log.info("Bot started. Polling…")

    while True:
        try:
            resp = _api("getUpdates", offset=offset, timeout=30)
            for update in resp.get("result", []):
                offset = update["update_id"] + 1
                if "callback_query" in update:
                    handle_callback(update["callback_query"])
                elif "message" in update:
                    handle_message(update["message"])
        except KeyboardInterrupt:
            log.info("Stopped.")
            break
        except Exception as e:
            log.error(f"Error: {e}")
            time.sleep(3)

if __name__ == "__main__":
    run()
