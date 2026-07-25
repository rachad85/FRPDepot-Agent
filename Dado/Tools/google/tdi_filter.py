"""Keeps TDI-side content out of Dado's Google results, logs, and receipts.

Rachad's personal Google account (rachad85@gmail.com) also carries TDI
banking/KYC threads - Aze uses it for Troy Dualam account-opening work (see
C:\\AgentTeam\\HermesProfiles\\aze\\skills\\tdi-operations\\references\\
personal-google-business-account-prep.md, which itself warns that a search
on this account can surface unrelated material across companies). When
Rachad scoped Dado's access to this same account (2026-07-24), he chose
"read+draft, TDI filtered" over broad access - so every Gmail/Drive result
Dado sees passes through here first.

This is a keyword screen, not a technical guarantee. Extend TDI_TERMS if a
banker name, TDI's registered entity name, or another identifying term
surfaces later.
"""
import re

# "dualam" alone is deliberate and load-bearing: it is the one distinctive
# token that catches every form at once - "Troy Dualam", the email/web domain
# troydualam.com (no space, which an earlier "troy dualam" term missed - live
# leak found 2026-07-24 during Rachad's first connected verification: mail
# from TDI's own domain was reaching Dado), and any "Dualam Inc" shorthand.
# Do NOT narrow this back to the two-word phrase.
# Bare "troy" is deliberately NOT here - it is a common first name and city,
# and would withhold unrelated personal mail.
TDI_TERMS = [
    "dualam",
    "tdi",
]

# Word-boundary forms of the terms above. "dualam" stays a bare substring so it
# still catches troydualam.com and "Dualam Inc". "tdi" is boundary-anchored:
# as a raw substring it fired on Turkish mail ("yurtdışı" lowercases through
# Python's Unicode casing into a run containing t-d-i), which would have walled
# off Rachad's own unrelated correspondence for nothing. \b still catches "TDI",
# "(TDI)", "TDI-1234" — every real reference — because punctuation is a boundary.
_TERM_PATTERNS = [
    re.compile(r"dualam", re.I),
    re.compile(r"(?<![A-Za-z0-9])tdi(?![A-Za-z0-9])", re.I),
]

# Markers of Troy Dualam that the terms above CANNOT catch, found 2026-07-24 by
# probing the built index for TDI signals the filter does not screen for. Each
# is deliberately narrow; every one was checked against real matches:
#   dumalac      - an observed MISSPELLING of Dualam in a live CRA/mortgage
#                  thread about "Troy Dumalac INC". 5 messages, all genuine.
#   troy_history - TDI's ADP project-hours database, named in TDI authority docs.
#   agentteam    - the C:\AgentTeam tree, i.e. TDI's own agent repo.
#   aze_*.json   - Aze's runtime artifacts (aze_active_task.json, aze_receipts
#                  .jsonl...). An EXTENSION is required: a bare "aze_" prefix
#                  matched an Amazon ad-tracking token "-AZE_q0BIWA".
#   ^aze_ name   - the same artifacts by filename.
#   Q26-####     - TDI's quote numbering (AECOM, Teck Metals, Ecolab...). This
#                  is the arm's-length-sensitive set: FRP Depot must not price
#                  against TDI's own internal quotes.
# NOT here, on purpose: bare "troy". Measured over the real corpus, 368 of its
# hits are "Troy – Elizabethtown" parcel deliveries to a person named Troy and
# only 5 are the company. Blocking it would wall off Rachad's own mail — the
# standing instruction is never to add a wall he did not ask for.
#
# BOUNDARIES: do NOT use \b here. Underscore is a word character, so \b never
# fires between "_" and "Q" — and Drive filenames are full of underscores.
# "RE_ RFQ for dual laminate dome and bottoms______________Q26-1526.msg" is a
# real TDI RFQ that \bq26 missed for exactly that reason. These use an explicit
# alphanumeric lookaround instead, which treats "_" as a boundary while still
# refusing a match inside a word (the Turkish "yurtdışı" case, where "tdi" is
# preceded by a letter).
_NOT_ALNUM_BEFORE = r"(?<![A-Za-z0-9])"
_NOT_ALNUM_AFTER = r"(?![A-Za-z0-9])"

DEEP_MARKERS = {
    "dualam": re.compile(r"dualam", re.I),
    "dumalac(misspelling)": re.compile(r"dumalac", re.I),
    "tdi": re.compile(_NOT_ALNUM_BEFORE + r"tdi" + _NOT_ALNUM_AFTER, re.I),
    "troy_history(TDI db)": re.compile(r"troy_history", re.I),
    "AgentTeam(TDI tree)": re.compile(r"agentteam", re.I),
    "aze artifact": re.compile(
        _NOT_ALNUM_BEFORE + r"aze_[a-z_]+\.(?:json|jsonl|md|py|txt)" + _NOT_ALNUM_AFTER, re.I),
    "Q26 quote number": re.compile(_NOT_ALNUM_BEFORE + r"q26-\d{3,}", re.I),
}
# Applied to a filename only. Anchored to a path/underscore boundary and
# requiring >=3 letters after "aze_", so a real artifact (aze_active_task,
# x_aze_receipts) matches while an ad-tracking token like "-AZE_q0BIWA" -- one
# letter then digits, preceded by a hyphen -- does not.
NAME_MARKERS = {
    "aze artifact filename": re.compile(r"(?:^|[\\/_])aze_[a-z]{3,}", re.I),
}


def is_tdi_flagged(*texts: str) -> bool:
    haystack = " ".join(t or "" for t in texts)
    return any(p.search(haystack) for p in _TERM_PATTERNS)


def deep_tdi_marker(*texts: str, name: str = "") -> str:
    """Return the name of the first deep marker that fires, else "".

    Used to re-screen an index that was built before these markers existed.
    Wider than is_tdi_flagged, and reports WHICH marker matched so a quarantine
    decision is auditable rather than a silent verdict.
    """
    haystack = " ".join(t or "" for t in texts)
    for label, pattern in DEEP_MARKERS.items():
        if pattern.search(haystack):
            return label
    for label, pattern in NAME_MARKERS.items():
        if name and pattern.search(name):
            return label
    return ""
