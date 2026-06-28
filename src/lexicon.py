"""
Clinical lexicon + Aho-Corasick automaton (Aho & Corasick, 1975).

Build a trie of every surface form once, add failure links, then scan any text in
O(n + z) — independent of lexicon size. This is what makes 100M patients feasible:
adding more terms NEVER slows a per-note scan.

Corrections vs. the naive draft, each verified against the real data:
  * Bare clinical adjectives added: notes say "Diabetic to Left foot", "Venous to
    Right lower leg", "Surgical to Abdominal wall" — the single word IS the type.
  * Location list extended: hip, cervical, abdominal wall, plantar, buttock... all
    appear in the real notes/assessments.
  * Drainage extended: "Min"/"minimal"/"scant"/"slight" appear in brief notes.
  * Word-boundary guard (see match_terms) kills substring false positives
    ("burn" inside "heartburn", "ssi" inside a word, etc.).
"""
from __future__ import annotations
from collections import deque

WOUND_TYPES = {
    "pressure_ulcer": ["pressure ulcer", "pressure injury", "pressure sore",
                       "decubitus", "bed sore", "pressure"],
    "diabetic_ulcer": ["diabetic foot ulcer", "diabetic ulcer", "neuropathic ulcer",
                       "dfu", "diabetic"],
    "venous_ulcer":   ["venous stasis ulcer", "venous ulcer", "stasis ulcer", "venous"],
    "arterial_ulcer": ["arterial ulcer", "ischemic ulcer", "arterial"],
    "surgical_site":  ["surgical site infection", "surgical wound", "surgical", "incision"],
    "abscess":        ["abscess"],
    "burn":           ["burn"],
}
LOCATIONS = {
    "sacrum":    ["sacrum", "sacral region", "sacral"],
    "coccyx":    ["coccyx", "coccygeal"],
    "heel":      ["heel", "calcaneal"],
    "ischium":   ["ischium", "ischial"],
    "trochanter":["trochanter", "trochanteric"],
    "foot":      ["foot"],
    "plantar":   ["plantar"],
    "toe":       ["toe"],
    "ankle":     ["ankle", "malleolus"],
    "lower_leg": ["lower leg", "calf", "shin"],
    "buttock":   ["buttock", "gluteal"],
    "hip":       ["hip"],
    "abdominal_wall": ["abdominal wall", "abdomen", "abdominal"],
    "cervical":  ["cervical"],
    "forearm":   ["forearm"],
}
DRAINAGE = {
    "none":     ["no drainage", "without drainage", "dry", "none"],
    "light":    ["minimal", "scant", "slight", "light", "small amount", "min"],
    "moderate": ["moderate"],
    "heavy":    ["copious", "profuse", "large amount", "heavy"],
}
LATERALITY = {"left": ["left", " l "], "right": ["right", " r "]}

# ── ICD-10 wound gate (CORRECTED & verified against real diagnoses.json) ───
# Prefixes here are ALWAYS active-wound when clinical_status == "active".
# Decoys deliberately excluded: E11.9 (diabetes, NO ulcer), I70.209 (atherosclerosis,
# unspecified, NO ulcer). startswith() is safe because no decoy shares these prefixes.
WOUND_ICD_PREFIXES = {
    "L89":     "pressure_ulcer",   # L89.319, L89.152, L89.302, L89.61x ...
    "L97":     "diabetic_ulcer",   # non-pressure chronic ulcer, lower limb
    "L98.4":   "chronic_ulcer",
    "I83.0":   "venous_ulcer",     # I83.012, I83.022 (varicose w/ ulcer)
    "I83.2":   "venous_ulcer",
    "E08.62":  "diabetic_ulcer", "E09.62": "diabetic_ulcer",
    "E10.62":  "diabetic_ulcer", "E11.62": "diabetic_ulcer",  # E11.621/.622 (NOT E11.9)
    "E13.62":  "diabetic_ulcer",
    "I70.23":  "arterial_ulcer", "I70.24": "arterial_ulcer",  # ...with ulceration (NOT I70.209)
    "I70.25":  "arterial_ulcer",
    "T81.3":   "surgical_site",  "T81.4": "surgical_site",    # T81.31XA wound disruption
    "L76":     "surgical_site",
    "L02":     "abscess",                                     # L02.211 abdominal-wall abscess
    "T20": "burn", "T21": "burn", "T22": "burn",              # T22.219A forearm burn
    "T23": "burn", "T24": "burn", "T25": "burn",
}


class AhoCorasick:
    """Compact pure-Python Aho-Corasick. Swap for `pyahocorasick` (C) at 100M scale."""
    def __init__(self):
        self.goto = [{}]; self.fail = [0]; self.out = [[]]

    def _new(self):
        self.goto.append({}); self.fail.append(0); self.out.append([])
        return len(self.goto) - 1

    def add(self, word, payload):
        node = 0
        for ch in word:
            node = self.goto[node].setdefault(ch, self._new())
        self.out[node].append((*payload, len(word)))

    def build(self):
        q = deque()
        for nxt in self.goto[0].values():
            self.fail[nxt] = 0; q.append(nxt)
        while q:
            u = q.popleft()
            for ch, nxt in self.goto[u].items():
                q.append(nxt)
                f = self.fail[u]
                while f and ch not in self.goto[f]:
                    f = self.fail[f]
                self.fail[nxt] = self.goto[f].get(ch, 0)
                self.out[nxt] += self.out[self.fail[nxt]]
        return self

    def iter_raw(self, text):
        node = 0
        for i, ch in enumerate(text):
            while node and ch not in self.goto[node]:
                node = self.fail[node]
            node = self.goto[node].get(ch, 0)
            for canonical, category, length in self.out[node]:
                yield category, canonical, i - length + 1, i + 1


def _build():
    ac = AhoCorasick()
    for table, cat in ((WOUND_TYPES, "wound_type"), (LOCATIONS, "location"),
                       (DRAINAGE, "drainage"), (LATERALITY, "laterality")):
        for canonical, forms in table.items():
            for form in forms:
                ac.add(form, (canonical, cat))
    return ac.build()


AUTOMATON = _build()   # built once at import


def _is_word_char(c: str) -> bool:
    return c.isalnum() or c == "_"


def match_terms(text: str):
    """Yield (category, canonical, start, end) with a word-boundary guard so
    substrings inside larger words are rejected. Preserves O(n + z)."""
    n = len(text)
    for cat, canonical, s, e in AUTOMATON.iter_raw(text):
        left_ok = s == 0 or not _is_word_char(text[s - 1])
        right_ok = e >= n or not _is_word_char(text[e])
        if left_ok and right_ok:
            yield cat, canonical, s, e