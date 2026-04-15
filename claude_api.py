"""Generate flashcard fields from a phrase using Claude API."""
import os, json, re
import anthropic

_client = None

def _get_client():
    global _client
    if _client is None:
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")
        _client = anthropic.Anthropic(api_key=key)
    return _client

PROMPT = """\
You are an English flashcard generator for a Ukrainian professional learner working in Sweden.

Given a word or phrase, create a flashcard. Return ONLY valid JSON, no markdown, no explanation:

{
  "subtype": "Vocabulary|Phrasal Verb|Idiom|Collocation|Business English|Everyday Phrase|Common Correction",
  "section": "Vocabulary (Single Words)|Business & Work|Idioms & Figurative Language|Everyday Conversation|Common Corrections & Awkward Phrases",
  "front": "Question that tests knowledge WITHOUT giving away the answer. For vocabulary/business: use a real context sentence then ask what the key word means. For everyday phrases: describe a situation and ask what to say. NEVER include the answer word in the front.",
  "back": "Clear definition starting with capital letter. → Example sentence using the word naturally.",
  "blank": "Take the example sentence from back and replace the key word with ___. Leave empty string if the phrase itself is the blank (circular).",
  "answer": "The word(s) that fill the blank. Leave empty string if blank is empty."
}

Rules:
- Front must NEVER contain the answer word or phrase
- Back definition must start with capital letter
- Example in back should feel natural, like something a native speaker would say
- For Business English: use workplace/analytics context when possible
- For Collocation: show make/do/have/take choices on front, correct one on back
- For Phrasal Verb: show it used in a sentence, blank the verb+particle\
"""

def generate_card(phrase: str):
    """Call Claude Haiku to generate card fields. Returns dict or None on failure."""
    try:
        client = _get_client()
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            messages=[{"role": "user", "content": f"{PROMPT}\n\nCreate a flashcard for: {phrase}"}]
        )
        text = msg.content[0].text.strip()
        m = re.search(r'\{.*\}', text, re.DOTALL)
        if m:
            text = m.group(0)
        card = json.loads(text)
        # Ensure all required fields exist
        for f in ("subtype", "section", "front", "back", "blank", "answer"):
            card.setdefault(f, "")
        return card
    except Exception as e:
        return None

if __name__ == "__main__":
    import sys
    phrase = " ".join(sys.argv[1:]) or "benchmark"
    print(f"Generating card for: {phrase}")
    card = generate_card(phrase)
    print(json.dumps(card, indent=2, ensure_ascii=False))
