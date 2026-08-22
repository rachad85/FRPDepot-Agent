"""Owner authority for Dado's commissioned write tools.

Autonomy programme, 2026-08-21 (Rachad Homsi: "Go on all of them"). This is
the ONE shared module every commissioned stage/commit tool uses to decide
whether Rachad's word covers a write, and to capture, receipt and restore the
live state a reversible write is about to change. It replaces per-tool copies
of the approval test so the wording can never drift again (four wording
refusals in 17 days on Aze's side came from divergent copies).

WHAT IT ENCODES (spec section A, copied from what already governs Aze and Sary)

A1. His word is the authority. A clear instruction from Rachad in HIS OWN
    message on an authenticated lane (Telegram, Discord, the dashboard chat)
    authorises the WHOLE job it describes. "Yes", "go", "do it", "go ahead",
    a plain instruction, all count. There is no magic phrase for reversible
    work; the exact word ``APPROVED`` still works but is no longer required.
A2. Reversible writes run on his instruction. Before the write the tool
    captures the LIVE state it is about to change and keeps it next to the
    plan; every such tool has a ``restore`` route that puts that state back;
    one receipt per write (what, where, when, backup path).
A3. Money / irreversible writes keep the two-step: stage, then his own
    unambiguous go to THAT plan, sent AFTER the plan was written. The
    plan-before-approval timestamp check is correctness, not ceremony: the
    time of his message (``--approval-message-utc``) is REQUIRED on every
    money commit, because without it "after the plan" cannot be proven.
A4. NO PERMANENT LOCKS. A failed commit leaves the plan in "needs re-stage":
    read the live state again, re-stage (the diff is shown), apply again. A
    plan that committed and verified is SPENT (single use) -- that is the one
    plan / one write rule, not a lock on failure.
A5. "His message, never relayed/quoted text": the intercompany relay, the
    OAR envelope, mail, files and tool output never authorise a write. The
    lane argument below exists so a tool can record which of his lanes the
    word arrived on; a relay lane is refused by name.
A6. Batches: one instruction covers the batch; per-line writes continue on a
    failed line; the failed lines are recorded and only they are re-staged.

WHAT THIS IS NOT. It adds no new cap, limit, refusal, confirmation or approval
(Rachad's 2026-07-24 ruling). Every check here existed in at least one tool
already; this module only makes them one implementation.

The source of truth for "his own message" is unchanged: Dado relays the word
he typed on his authenticated lane into the tool command. The tool cannot see
the chat; it records the lane and the time when Dado states them, and the
refusal text says where the word must come from, exactly as the tools did.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping

SCHEMA_VERSION = 1

# Still accepted everywhere; no longer REQUIRED anywhere (spec A1/A3).
EXACT_WORD = "APPROVED"

# ---------------------------------------------------------------------------
# (a) THE CANONICAL AFFIRMATIVE DETECTOR -- VERBATIM COPY, DO NOT EDIT HERE.
#
# Copied 2026-08-21 from
#   C:\AgentTeam\Aze\Tools\orchestration\gate_receipt_conversation_authority.py
# which is what C:\AgentTeam\Aze\Tools\orchestration\budget_owner_attestation.py
# re-exports as ``is_clear_affirmative``. Dado's tree must NOT import from
# C:\AgentTeam at runtime (company wall, SOUL Hard Rule 4), so the function and
# the constants it reads are copied here and MUST STAY IDENTICAL to the Aze
# copy. test_owner_authority.py pins the SHA-256 of each function's source as
# it stood in the Aze file on the copy date; change the Aze copy first, then
# this one, then the pins, never the other way round.
#
#   _normalize_reply          sha256 b68990308e5d9b94f1e2600896069a8486f5ec1f2fe01fd652585df3b9b494e0
#   is_clear_affirmative      sha256 5378578fb083395d02ca345d077f93d1644a2e1baf082792c0552db9cb654507
#   has_condition_or_negation sha256 af15e30d91de13287ba3511ca450153d3660c6afa29ccfd1ebcaa433c374866e
# ---------------------------------------------------------------------------
_AFFIRMATIVE_NORMAL_FORMS = {
    "yes",
    "yes please",
    "yes go ahead",
    "go",
    "go ahead",
    "please go ahead",
    "proceed",
    "please proceed",
    "approved",
    "approve it",
    "do it",
    "okay",
    "ok",
    "sounds good",
    "that works",
    "green light",
}
# Natural-language affirmatives, 2026-08-06. These sets are kept VERBATIM
# in parity with gateway\telegram_authority.py in the hermes-agent tree (the
# 2026-08-04 relaxation Rachad ordered: approval must never depend on
# reciting an exact phrase). Four wording-refusal incidents in 17 days came
# from divergent copies of this test - the fourth was the NanoStrip budget
# open refusing "PLEASE PROCEED AND OPEN BUDGET QUOTE" because THIS copy
# still capped natural approvals at 5 allowlisted words. One semantics now;
# test_approval_parser_parity.py fails the suite if the copies drift again.
# Any ONE of these words is an unconditional yes on its own.
_AFFIRMATIVE_WORDS = frozenset(
    {
        "yes", "yep", "yeah", "yup", "ya", "yea", "aye",
        "ok", "okay", "okey", "sure", "certainly", "absolutely", "definitely",
        "proceed", "approved", "approve", "confirm", "confirmed", "confirming",
        "agreed", "agree", "correct", "perfect", "exactly", "affirmative",
        "go", "granted", "authorized", "authorised", "accept", "accepted",
    }
)
# Multi-word ways of saying the same thing.
_AFFIRMATIVE_PHRASES = (
    "go ahead", "green light", "do it", "run it", "send it", "issue it",
    "that works", "sounds good", "looks good", "all good", "fine by me",
    "please do", "carry on", "keep going", "make it so", "let's do it",
    "lets do it", "you may", "you can",
)
# Anything here means the reply is negative or CONDITIONAL, so it is not an
# unconditional approval no matter what else it contains. "yes but change the
# design temperature" must never authorise an engine run.
_AFFIRMATIVE_BLOCKERS = frozenset(
    {
        "no", "not", "dont", "don't", "doesnt", "doesn't", "never", "nope",
        "hold", "wait", "stop", "pause", "cancel", "abort", "reject", "rejected",
        "but", "unless", "if", "except", "however", "instead", "though",
        "change", "revise", "first", "before", "after", "once", "when",
        "maybe", "perhaps", "probably", "almost", "unsure", "recheck", "check",
    }
)
# Phrases that name ANOTHER quoting gate's action. Seeing one means the reply
# is that gate's command, not a plain yes to the armed one.
_OTHER_GATE_ACTIONS = (
    "generate quote",
    "generate the quote",
    "issue quote",
    "issue the quote",
)
_LEGACY_START_RE = re.compile(
    r"^start (?:q26-\d{4}|b26-(?:00[1-9]|0[1-9]\d|[1-9]\d{2,3}))$"
)
# Loose on purpose: any case-id-shaped token disqualifies a reply from the
# GENERIC-yes path (it is a named-case command; exact/START paths own those).
_CASE_ID_MENTION_RE = re.compile(r"\b[qb]\d{2}-\d{3,4}\b")


def _normalize_reply(value: str) -> str:
    text = value.casefold().strip()
    if "?" in text or len(text) > 80:
        return ""
    text = re.sub(r"[.!]+$", "", text)
    text = re.sub(r"[,;:]+", " ", text)
    return " ".join(text.split())


def is_clear_affirmative(value: Any) -> bool:
    """Accept a short, UNCONDITIONAL reply to the single armed case gate.

    2026-08-06 rewrite (fourth wording-refusal incident; the NanoStrip budget
    open refused "PLEASE PROCEED AND OPEN BUDGET QUOTE" while "please proceed"
    alone was canonical). Semantics now match the 2026-08-04 relaxation in
    gateway\\telegram_authority.py exactly, plus this lane's legacy
    ``START <case>`` form: what does the real safety work is the BINDING (one
    armed challenge, one case/revision, expiry window, Rachad's own chat),
    never the wording. Strictness kept where it matters:

      * a blocker token (no/not/hold/wait/but/unless/if/change/first ...)
        refuses outright - "yes but change the SG" is not an approval;
      * a question mark or an over-long reply (>80 chars) refuses;
      * naming ANOTHER gate's action ("generate quote") is that gate's
        command, never a generic yes;
      * otherwise an affirmative anchor word or phrase must be present -
        restating the approved action around it is fine.
    """
    if not isinstance(value, str):
        return False
    normalized = _normalize_reply(value)
    if normalized and (
        normalized in _AFFIRMATIVE_NORMAL_FORMS
        or _LEGACY_START_RE.fullmatch(normalized)
    ):
        return True
    text = value.casefold().strip()
    if "?" in text or len(text) > 80:
        return False
    text = re.sub(r"[^\w\s'-]+", " ", text)
    tokens = text.split()
    if not tokens:
        return False
    if any(token.strip("'-") in _AFFIRMATIVE_BLOCKERS for token in tokens):
        return False
    normalised = " ".join(tokens)
    if any(action in normalised for action in _OTHER_GATE_ACTIONS):
        return False
    # A reply that NAMES a case id is that case's command, never a generic
    # yes - "yes, for Q26-9995" must not be consumable by whatever gate
    # happens to be armed. Exact-text and START paths handle named cases.
    if _CASE_ID_MENTION_RE.search(normalised):
        return False
    if any(token in _AFFIRMATIVE_WORDS for token in tokens):
        return True
    return any(phrase in normalised for phrase in _AFFIRMATIVE_PHRASES)


def has_condition_or_negation(value: Any) -> bool:
    """True when the reply carries a blocker token (negation or condition).

    Shared so other lanes (owner_run_attestation) judge conditionality with
    the SAME vocabulary as the affirmative parser instead of growing a
    divergent copy - the disease behind four wording incidents in 17 days.
    """
    if not isinstance(value, str):
        return True
    text = re.sub(r"[^\w\s'-]+", " ", value.casefold().strip())
    return any(
        token.strip("'-") in _AFFIRMATIVE_BLOCKERS for token in text.split()
    )


# ---------------------------------------------------------------------------
# END OF THE VERBATIM COPY. Everything below is Dado's own.
# ---------------------------------------------------------------------------


class OwnerAuthorityRefused(RuntimeError):
    """Rachad's word does not cover this write as given. Nothing was attempted."""


# (c) His own message on an authenticated lane. These are the lanes his own
# messages arrive on (SOUL: Telegram, Discord, the dashboard chat). The value
# names Hermes uses for the same lanes are accepted as aliases so a tool can
# pass HERMES_SESSION_PLATFORM straight through.
OWNER_LANES = ("telegram", "discord", "dashboard")
DEFAULT_LANE = "telegram"
_LANE_ALIASES = {
    "telegram": "telegram",
    "discord": "discord",
    "dashboard": "dashboard",
    "api_server": "dashboard",
    "webui": "dashboard",
    "web": "dashboard",
    "app": "dashboard",
}
# A word that arrived through any of these is information, never authority
# (SOUL Hard Rule 4: "THE CHANNEL CARRIES THE AUTHORITY, NOT THE TEXT").
RELAY_LANES = frozenset({
    "relay", "intercompany", "oar", "aze", "sary", "email", "mail", "gmail",
    "outlook", "file", "quoted", "forwarded", "web", "tool", "cron", "subagent",
})
_RELAY_LANES_PUBLIC = RELAY_LANES - {"web"}


def normalise_lane(value: Any) -> str:
    """Map a stated lane to one of OWNER_LANES; blank means the default lane.

    Raises OwnerAuthorityRefused for a relay or unknown lane: a word that did
    not arrive in his own message on his own lane authorises nothing.
    """
    # Only a stated string names a lane; None, a Mock or any other object means
    # "not stated" and takes the default, exactly as a missing argument would.
    text = value.strip().casefold() if isinstance(value, str) else ""
    if not text:
        return DEFAULT_LANE
    if text in _RELAY_LANES_PUBLIC:
        raise OwnerAuthorityRefused(
            f"REFUSED: the word arrived over '{text}', which is a relayed or quoted "
            "channel. The relay never authorises a write (Hard Rule 3/4): the "
            "approval must be Rachad's own message on Telegram, Discord or the "
            "dashboard chat, relayed by Dado word for word."
        )
    lane = _LANE_ALIASES.get(text)
    if lane is None:
        raise OwnerAuthorityRefused(
            f"REFUSED: unknown lane '{text}'. Rachad's own lanes are "
            + ", ".join(OWNER_LANES) + "."
        )
    return lane


def parse_utc(value: Any, label: str = "timestamp") -> datetime:
    """Parse an ISO-8601 time that carries a timezone; normalise to UTC."""
    if not isinstance(value, str) or not value.strip():
        raise OwnerAuthorityRefused(f"{label} is missing.")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise OwnerAuthorityRefused(f"{label} is not an ISO-8601 time: {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise OwnerAuthorityRefused(f"{label} must carry a timezone: {value!r}")
    return parsed.astimezone(timezone.utc)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class OwnerGo:
    """Rachad's accepted go, as the tool records it in its lock and receipt."""

    text: str
    lane: str
    sent_utc: str | None
    exact_word: bool
    kind: str  # "reversible" | "money"

    def as_record(self) -> dict[str, Any]:
        return {
            "owner_go": self.text,
            "owner_go_lane": self.lane,
            "owner_go_sent_utc": self.sent_utc or "not stated",
            "owner_go_exact_word": self.exact_word,
            "owner_go_kind": self.kind,
            "owner_go_schema": SCHEMA_VERSION,
        }


def _clean_word(text: Any) -> str:
    if not isinstance(text, str) or not text.strip():
        raise OwnerAuthorityRefused(
            "REFUSED: no approval text was given. Relay Rachad's own words from his "
            "message on Telegram, Discord or the dashboard chat; Dado never supplies them."
        )
    stripped = text.strip()
    if any(ch in stripped for ch in "\r\n\x00"):
        raise OwnerAuthorityRefused(
            "REFUSED: the approval text spans several lines. Relay the one line he "
            "answered with."
        )
    return stripped


def require_owner_go(text: Any, *, lane: Any = None, what: str = "this plan") -> OwnerGo:
    """A1/A2: any clear affirmative in his own message covers a REVERSIBLE write.

    ``APPROVED`` still works. "yes", "go ahead", "do it", "proceed" and a plain
    instruction all count. A conditional or negated reply ("yes but ...",
    "wait", "not yet") is refused, as is a question. No timestamp rule: his
    original instruction covers the whole job and its re-stages.
    """
    word = _clean_word(text)
    lane_name = normalise_lane(lane)
    if not is_clear_affirmative(word):
        raise OwnerAuthorityRefused(
            f"REFUSED: {word!r} is not a clear, unconditional go for {what}. Rachad's "
            "own 'yes', 'go ahead', 'do it', 'proceed' or APPROVED in his message on "
            "Telegram, Discord or the dashboard chat all count; a question, a condition "
            "('yes but ...') or a 'wait' does not. Resolve his condition, then relay his "
            "plain go. Dado never supplies it."
        )
    return OwnerGo(
        text=word, lane=lane_name, sent_utc=None,
        exact_word=(word == EXACT_WORD), kind="reversible",
    )


def require_owner_go_after_plan(
    text: Any,
    *,
    plan_created_utc: Any,
    plan_expires_utc: Any = None,
    sent_utc: Any = None,
    lane: Any = None,
    what: str = "this plan",
) -> OwnerGo:
    """A3/A5: a MONEY or IRREVERSIBLE write needs his unambiguous go to THAT
    plan, sent AFTER the plan was written.

    Exact ``APPROVED`` still works; "yes go ahead" to the shown plan counts.
    The time of his message (``sent_utc``, ISO-8601 with a timezone) is
    REQUIRED: it must fall after ``plan_created_utc`` and before
    ``plan_expires_utc``. A word sent before the plan existed cannot approve
    it, and a go whose time is not given cannot be shown to have come after
    the plan, so it is refused and the refusal names the plan's creation time
    and the flag to pass. This is the pre-existing plan-before-approval rule
    (spec A3/A5), which the J26-403 tool pioneered on 2026-08-21 -- not a new
    wall. Nothing is recorded as "not stated" on a money write.
    """
    word = _clean_word(text)
    lane_name = normalise_lane(lane)
    if not is_clear_affirmative(word):
        raise OwnerAuthorityRefused(
            f"REFUSED: {word!r} is not Rachad's unambiguous go to {what}. For a money "
            "or irreversible write he must answer THE SHOWN PLAN, after it was staged, "
            "in his own message: 'yes go ahead', 'approved', 'do it' all count; a "
            "question, a condition or a 'wait' does not. Commissioning the tool and a "
            "'proceed' to BUILD it are not approval of a plan. Dado never supplies it."
        )
    created = parse_utc(plan_created_utc, "plan creation time")
    # Only a stated string is a time; None, blank, a Mock or any other object
    # means the time was not given, and "after the plan" cannot be proven.
    if not isinstance(sent_utc, str) or not sent_utc.strip():
        raise OwnerAuthorityRefused(
            f"REFUSED: the time of Rachad's approval message was not given, so his go to "
            f"{what} cannot be shown to have come AFTER the plan was written (this plan was "
            f"created at {created.isoformat()}). Pass --approval-message-utc with the "
            "ISO-8601 time of his message, timezone included (for example "
            "2026-08-21T14:05:00+00:00), read from his message on Telegram, Discord or the "
            "dashboard chat. Nothing was written."
        )
    moment = parse_utc(sent_utc, "approval message time")
    if moment < created:
        raise OwnerAuthorityRefused(
            f"REFUSED: the approval message is timestamped {moment.isoformat()}, "
            f"BEFORE this plan was created at {created.isoformat()}. A word sent "
            "before the plan existed cannot approve it. Nothing was written."
        )
    if plan_expires_utc not in (None, ""):
        expires = parse_utc(plan_expires_utc, "plan expiry")
        if moment >= expires:
            raise OwnerAuthorityRefused(
                f"REFUSED: the approval message is timestamped {moment.isoformat()}, "
                f"at or after this plan expired at {expires.isoformat()}. Re-stage "
                "and ask for his go to the new plan."
            )
    return OwnerGo(
        text=word, lane=lane_name, sent_utc=moment.isoformat(),
        exact_word=(word == EXACT_WORD), kind="money",
    )


def add_owner_go_arguments(parser: Any, *, money: bool = False) -> None:
    """Attach the shared ``--approval`` family to a commit/apply/restore parser.

    ``--approval`` keeps its name so every existing command line still works.
    ``--approval-lane`` (telegram | discord | dashboard) is optional and
    recorded in the receipt. On money tools ``--approval-message-utc`` is
    REQUIRED (A3/A5: his go must be shown to have come AFTER the plan).
    """
    parser.add_argument(
        "--approval", required=True,
        help="Rachad's own words from his message (APPROVED, yes, go ahead, do it ...).",
    )
    parser.add_argument(
        "--approval-lane", dest="approval_lane", default=None,
        help="Which of his lanes the word arrived on: telegram (default), discord, dashboard.",
    )
    if money:
        parser.add_argument(
            "--approval-message-utc", dest="approval_message_utc", required=True,
            help="ISO-8601 time of his approval message, timezone included; it must be "
                 "after the plan was created. Required on every money commit.",
        )


# ---------------------------------------------------------------------------
# A4 -- attempt records without permanent locks
# ---------------------------------------------------------------------------

STATUS_IN_FLIGHT = "in_flight"
STATUS_COMMITTED = "committed_verified"
STATUS_SPENT = STATUS_COMMITTED
# The write did not happen, or happened on some lines only, and the plan is
# bound to stale live state: read the live state again, re-stage, apply again.
STATUS_NEEDS_RESTAGE = "needs_restage"
# A write left the building and its outcome is unknown: the re-stage's live
# read settles what landed before anything is attempted again.
STATUS_INDETERMINATE = "indeterminate_needs_restage"
TERMINAL_STATUSES = frozenset({STATUS_COMMITTED, STATUS_NEEDS_RESTAGE, STATUS_INDETERMINATE})


def attempt_record(
    status: str,
    *,
    plan_sha256: str,
    action: str,
    go: OwnerGo | None = None,
    reason: str | None = None,
    completed: Any = None,
    failed: Any = None,
    pending: Any = None,
    **extra: Any,
) -> dict[str, Any]:
    """The lock/attempt file body every tool writes. No 'permanent', no 'no_retry'."""
    record: dict[str, Any] = {
        "plan_sha256": plan_sha256,
        "action": action,
        "status": status,
        "updated_utc": utc_now().isoformat(),
        "permanent_lock": False,
        "next_step": next_step_for(status),
    }
    if go is not None:
        record.update(go.as_record())
    if reason is not None:
        record["reason"] = str(reason)[:2000]
    if completed is not None:
        record["completed"] = completed
    if failed is not None:
        record["failed"] = failed
    if pending is not None:
        record["pending"] = pending
    record.update(extra)
    return record


def next_step_for(status: str) -> str:
    if status == STATUS_COMMITTED:
        return "done; this plan is spent (one plan, one write). Stage a new plan for any further change."
    if status == STATUS_NEEDS_RESTAGE:
        return ("re-stage: read the live state again, stage a new plan (the diff is shown), "
                "apply again. His original instruction still covers the retry unless the scope changed.")
    if status == STATUS_INDETERMINATE:
        return ("a write left the building with an unknown outcome: re-stage reads the live "
                "state first and shows what actually landed before anything is attempted again.")
    if status == STATUS_IN_FLIGHT:
        return "a write is in flight."
    return "see reason"


def explain_outcome(
    tool_label: str,
    status: str,
    reason: Any,
    *,
    money: bool = False,
    completed: Any = None,
    failed: Any = None,
    pending: Any = None,
) -> str:
    """Owner-facing sentence for a failed or indeterminate commit (no lock talk)."""
    bits = [f"{tool_label}: {status.replace('_', ' ')} [{status}]. {reason}"]
    if completed:
        bits.append(f"Verified writes that remain live: {completed}.")
    if failed:
        bits.append(f"Failed lines: {failed}.")
    if pending:
        bits.append(f"Untouched lines: {pending}.")
    if status == STATUS_INDETERMINATE:
        bits.append("The outcome of the last write is unknown; the re-stage reads the live "
                    "state and shows what landed.")
    if money:
        bits.append("This plan is not retried silently: re-stage it and ask Rachad for his go "
                    "to the NEW plan.")
    else:
        bits.append("Nothing stays locked: re-stage (the diff is shown) and apply again; "
                    "his original instruction still covers the retry unless the scope changed.")
    return " ".join(bits)


def refuse_replay(tool_error: type, lock_record: Mapping[str, Any] | None, *, what: str = "plan") -> None:
    """Turn an existing attempt record into the right refusal (spent vs re-stage)."""
    status = str((lock_record or {}).get("status") or "")
    if status == STATUS_COMMITTED:
        raise tool_error(
            f"REFUSED: this {what} was already committed and verified; it is spent "
            "(one plan, one write) and cannot be replayed. Stage a new plan for any further change."
        )
    if status in (STATUS_NEEDS_RESTAGE, STATUS_INDETERMINATE):
        raise tool_error(
            f"REFUSED: this {what} ended '{status}' on its last attempt and is bound to "
            "stale live state. Re-stage it (the live state is read again and the diff is "
            "shown) and apply the NEW plan; nothing stays locked."
        )
    raise tool_error(
        f"REFUSED: this {what} already entered commit (status '{status or 'in_flight'}'). "
        "If that attempt is still running, wait for it; otherwise re-stage and apply the new plan."
    )


def read_json_if_exists(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def write_json_exclusive_or_replace(path: Path, value: Mapping[str, Any], *, exclusive: bool) -> None:
    """Create-only when exclusive (the first attempt record), else overwrite in place."""
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_TRUNC)
    descriptor = os.open(str(path), flags, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(dict(value), ensure_ascii=True, indent=2) + "\n")


# ---------------------------------------------------------------------------
# (d) live-state capture, restore records and receipts
# ---------------------------------------------------------------------------


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_of(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def live_state_path(plan_path: Path | str) -> Path:
    """Where the captured live state lives: next to the plan, same stem."""
    path = Path(plan_path)
    return path.with_name(path.stem + ".live-before.json")


def restore_record_path(plan_path: Path | str) -> Path:
    path = Path(plan_path)
    return path.with_name(path.stem + ".restore.json")


def capture_live_state(
    plan_path: Path | str,
    state: Mapping[str, Any],
    *,
    what: str,
    where: str,
    plan_sha256: str | None = None,
) -> Path:
    """A2: keep the live state a write is about to change next to the plan.

    Written atomically (temp + replace) so a crash mid-write cannot leave a
    half file that a later restore would trust. Returns the backup path.
    """
    path = live_state_path(plan_path)
    record = {
        "schema_version": SCHEMA_VERSION,
        "captured_utc": utc_now().isoformat(),
        "what": what,
        "where": where,
        "plan": str(Path(plan_path)),
        "plan_sha256": plan_sha256,
        "state": dict(state),
        "state_sha256": sha256_of(dict(state)),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def load_live_state(plan_path: Path | str, tool_error: type = OwnerAuthorityRefused) -> dict[str, Any]:
    """Read the captured live state back and prove it was not edited."""
    path = live_state_path(plan_path)
    record = read_json_if_exists(path)
    if record is None:
        raise tool_error(
            f"No captured live state exists for this plan ({path.name}); the write was "
            "never applied through this tool, so there is nothing to restore."
        )
    state = record.get("state")
    if not isinstance(state, dict) or record.get("state_sha256") != sha256_of(state):
        raise tool_error(f"The captured live state {path.name} is malformed or was edited; refusing to restore from it.")
    return record


def record_restore(plan_path: Path | str, result: Mapping[str, Any]) -> Path:
    path = restore_record_path(plan_path)
    body = {"schema_version": SCHEMA_VERSION, "restored_utc": utc_now().isoformat(), **dict(result)}
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def write_receipt(
    receipts_path: Path | str,
    action: str,
    *,
    what: str,
    where: str,
    backup: Path | str | None = None,
    plan: Path | str | None = None,
    plan_sha256: str | None = None,
    go: OwnerGo | None = None,
    extra: Mapping[str, Any] | None = None,
) -> None:
    """A2: one receipt per write -- what, where, when, backup path.

    Same file and same line shape (ts/action/evidence) as every existing
    receipt in Dado's tree, so nothing that reads receipts.jsonl changes.
    """
    evidence_bits = [f"what={what}", f"where={where}"]
    if backup is not None:
        evidence_bits.append(f"backup={backup}")
    if plan is not None:
        evidence_bits.append(f"plan={plan}")
    if plan_sha256 is not None:
        evidence_bits.append(f"sha256={plan_sha256}")
    if go is not None:
        evidence_bits.append(f"go_lane={go.lane}")
        evidence_bits.append(f"go_sent={go.sent_utc or 'not stated'}")
    if extra:
        evidence_bits.extend(f"{key}={value}" for key, value in extra.items())
    row = {
        "ts": utc_now().isoformat(),
        "action": action,
        "evidence": "; ".join(evidence_bits),
    }
    path = Path(receipts_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=True) + "\n")


# ---------------------------------------------------------------------------
# A6 -- batch bookkeeping
# ---------------------------------------------------------------------------


class BatchProgress:
    """Per-line outcome for a non-atomic batch: continue past a failed line."""

    def __init__(self, keys: list[str]) -> None:
        self.keys = list(keys)
        self.completed: list[str] = []
        self.failed: dict[str, str] = {}

    def done(self, key: str) -> None:
        self.completed.append(key)

    def fail(self, key: str, reason: Any) -> None:
        self.failed[key] = str(reason)[:1000]

    @property
    def pending(self) -> list[str]:
        touched = set(self.completed) | set(self.failed)
        return [key for key in self.keys if key not in touched]

    @property
    def status(self) -> str:
        if not self.failed and not self.pending:
            return STATUS_COMMITTED
        return STATUS_NEEDS_RESTAGE

    def summary(self) -> dict[str, Any]:
        return {
            "completed": list(self.completed),
            "failed": dict(self.failed),
            "pending": self.pending,
            "restage_only": sorted(set(self.failed) | set(self.pending), key=self.keys.index),
        }
