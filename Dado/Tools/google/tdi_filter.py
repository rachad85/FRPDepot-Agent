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


def is_tdi_flagged(*texts: str) -> bool:
    haystack = " ".join(t or "" for t in texts).lower()
    return any(term in haystack for term in TDI_TERMS)
