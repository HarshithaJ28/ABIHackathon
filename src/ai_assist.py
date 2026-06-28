"""OPTIONAL. Runs ONLY on still-missing required fields. The model must return a
value AND a verbatim quote; we verify the quote is a literal substring of the note.
Verified fields are tagged 'guessed' -> the decision lattice caps them at
flag_for_review. AI can upgrade missing->review, never review->bill. No-op if no client."""
from __future__ import annotations
from src.extract import REQUIRED


def fill_gaps(note_text: str, fused: dict, llm_call=None) -> dict:
    if llm_call is None or not note_text:
        return fused
    missing = [f for f in REQUIRED if fused["fields"][f]["confidence"] == "missing"]
    if not missing:
        return fused
    for field, sug in (llm_call(note_text, missing) or {}).items():
        quote = (sug or {}).get("quote", "")
        if quote and quote.lower() in note_text.lower():        # grounding check
            fused["fields"][field] = {"value": sug["value"], "confidence": "guessed",
                                      "source": "ai", "evidence": f"AI(verified):'{quote[:50]}'"}
    return fused