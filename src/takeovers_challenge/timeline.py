"""Fold movements into cap-table states, reconciled against what the documents themselves say the cap table is.

Movements (increases, decreases, transfers, entries, exits) change the state. Snapshots (statutes article
"Capital social", répartition tables, attendance sheets, "associée unique") are checked against it: a complete
holder list that disagrees replaces the reconstructed holders, and the disagreement is recorded as a check.
New shares whose subscriber is not named go to an explicit "Unidentified holders" bucket instead of being guessed.
Entries and exits are derived by diffing consecutive states, so a transfer and an entry are never applied twice.
Mentions that carry nothing to apply (no figures, no parties, no change) are skipped and reported, not exported.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field, replace

from .consolidate import Grounding, Movement, class_tokens, ground, normalize_name, same_party
from .corpus import Document
from .schemas import CapitalSnapshot, DocExtraction

UNIDENTIFIED = "Unidentified holders"
CAPITAL_CODES = {"CAPITAL_INCREASE", "CAPITAL_DECREASE"}
_ORDER = {
    "CAPITAL_DUAL_CLASS": 0,
    "CAPITAL_INCREASE": 1,
    "CAPITAL_DECREASE": 1,
    "SHAREHOLDER_SHARE_TRANSFER": 2,
    "SHAREHOLDER_ENTRY": 3,
    "SHAREHOLDER_END": 4,
}
_COMPANY = re.compile(r"\b(SAS|SARL|SA|SOCIETE|STE|FPCI|FCPI|FCPR|FIP|FONDS|VENTURE|HOLDING|GROUPE?|PARTNERS|FINANCIERE)\b")
_PERSON = re.compile(r"\b(MONSIEUR|MADAME|MADEMOISELLE|MME|MLLE|MR)\b")


def guess_kind(name: str) -> str:
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().upper()
    if _COMPANY.search(text):
        return "COMPANY"
    if _PERSON.search(text) or re.fullmatch(r"[A-Z][a-z]+(?:[- ][A-Z][a-z]+)* [A-Z][A-Z' -]+", name.strip()):
        return "PERSON"
    return "UNKNOWN"


def _plus(a: int | None, b: int | None) -> int | None:
    return None if a is None or b is None else a + b


def _class_label(share_class: str | None) -> str:
    return "/".join(sorted(class_tokens(share_class)))


def _bucket_key(share_class: str | None) -> str:
    return f"~unidentified:{_class_label(share_class)}"


@dataclass
class Holding:
    name: str
    kind: str = "UNKNOWN"
    siren: str | None = None
    shares: int | None = None
    share_class: str | None = None
    note: str | None = None


@dataclass
class SnapshotRef:
    doc: Document
    snapshot: CapitalSnapshot
    grounding: Grounding

    @property
    def as_of(self) -> str:
        return self.snapshot.as_of or self.doc.deposit_date

    @property
    def label(self) -> str:
        return f"{self.doc.inpi_id} p{self.grounding.page}"


@dataclass
class DerivedEvent:
    code: str
    date: str
    grounding: Grounding
    payload: dict
    note: str
    mechanism_event_ids: list[str] = field(default_factory=list)
    event_id: str = ""


@dataclass
class State:
    as_of: str | None = None
    capital_eur: float | None = None
    shares_total: int | None = None
    nominal_eur: float | None = None
    holdings: dict[str, Holding] = field(default_factory=dict)
    caused_by: list[str] = field(default_factory=list)
    derived: list[DerivedEvent] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    evidence: list[SnapshotRef] = field(default_factory=list)

    def clone(self) -> State:
        return State(
            self.as_of,
            self.capital_eur,
            self.shares_total,
            self.nominal_eur,
            {k: replace(h) for k, h in self.holdings.items()},
        )

    def named(self) -> dict[str, Holding]:
        return {k: h for k, h in self.holdings.items() if not k.startswith("~")}

    def fingerprint(self) -> tuple:
        return (
            self.capital_eur,
            self.shares_total,
            self.nominal_eur,
            tuple(sorted((k, h.shares) for k, h in self.holdings.items())),
        )


def collect_snapshots(extractions: list[tuple[Document, DocExtraction]]) -> list[SnapshotRef]:
    return [SnapshotRef(doc, snap, ground(doc, snap.evidence)) for doc, ex in extractions for snap in ex.snapshots]


class Folder:
    def __init__(self, aliases: dict[str, str] | None = None):
        self.aliases = aliases or {}
        self.states: list[State] = []
        self.issues: list[str] = []
        self.skipped: dict[str, str] = {}
        self.unmatched: list[SnapshotRef] = []
        self._preferred_bucket: str | None = None

    # ---- holdings -------------------------------------------------------------------------------------------

    def find(self, state: State, name: str | None) -> str | None:
        if not name:
            return None
        return next((k for k, h in state.named().items() if same_party(h.name, name, self.aliases)), None)

    def add(self, state: State, name: str, shares: int | None, *, kind=None, siren=None, share_class=None) -> None:
        key = self.find(state, name)
        if key is None:
            key = normalize_name(name, self.aliases)
            state.holdings[key] = Holding(name, kind or guess_kind(name), siren, shares, share_class)
            return
        h = state.holdings[key]
        h.shares = _plus(h.shares, shares)
        if h.kind == "UNKNOWN" and kind:
            h.kind = kind
        h.siren = h.siren or siren

    def to_bucket(self, state: State, shares: int | None, share_class: str | None, note: str) -> None:
        key, label = _bucket_key(share_class), _class_label(share_class)
        name = f"{UNIDENTIFIED} ({label} shares)" if label else UNIDENTIFIED
        bucket = state.holdings.setdefault(key, Holding(name, "UNKNOWN", None, 0, label or None))
        bucket.shares = _plus(bucket.shares, shares)
        bucket.note = "; ".join(filter(None, [bucket.note, note]))

    def from_bucket(self, state: State, shares: int) -> bool:
        keys = [k for k in state.holdings if k.startswith("~")]
        keys.sort(key=lambda k: (k != self._preferred_bucket, -(state.holdings[k].shares or 0)))
        for key in keys:
            bucket = state.holdings[key]
            if bucket.shares is not None and bucket.shares >= shares:
                bucket.shares -= shares
                if bucket.shares == 0:
                    del state.holdings[key]
                return True
        return False

    def take(self, state: State, name: str | None, shares: int | None, context: str) -> int | None:
        """Remove shares from a holder (all of them if `shares` is None). Returns how many left, when known."""
        key = self.find(state, name)
        if key is None:
            if shares is not None and self.from_bucket(state, shares):
                state.notes.append(f"{context}: {name} was not identified before; {shares} shares taken from unidentified holders")
                return shares
            state.notes.append(f"{context}: {name} holds no identified shares in the reconstructed cap table")
            return shares
        h = state.holdings[key]
        if shares is None:
            del state.holdings[key]
            return h.shares
        if h.shares is not None and h.shares < shares:
            state.checks.append(f"{context}: {h.name} holds {h.shares} shares and cannot part with {shares}")
        h.shares = None if h.shares is None else max(h.shares - shares, 0)
        if h.shares == 0:
            del state.holdings[key]
        return shares

    # ---- movements ------------------------------------------------------------------------------------------

    def _skip(self, m: Movement, reason: str) -> bool:
        self.skipped[m.event_id] = reason
        return False

    def _capital(self, state: State, m: Movement, sign: int) -> int | None:
        amount, after, before = m.value("amount_eur"), m.value("capital_after_eur"), state.capital_eur
        if after is None and None not in (before, amount):
            after = before + sign * amount
        if amount is None and None not in (before, after):
            amount = abs(after - before)
        if None not in (before, amount, after) and round(before + sign * amount) != round(after):
            state.checks.append(
                f"{m.event_id}: capital before {before:,.0f} {'+' if sign > 0 else '-'} {amount:,.0f} "
                f"!= capital after {after:,.0f} stated in {m.primary.doc.inpi_id}"
            )
        nominal = m.value("nominal_eur") or state.nominal_eur or (before / state.shares_total if before and state.shares_total else None)
        delta = m.value("shares_delta") or (round(amount / nominal) if amount and nominal else None)
        delta = None if delta is None else abs(delta)  # models sometimes sign cancelled shares
        state.capital_eur = after if after is not None else before
        state.nominal_eur = nominal
        state.shares_total = None if state.shares_total is None or delta is None else state.shares_total + sign * delta
        return delta

    def apply(self, state: State, m: Movement, same_day: list[Movement]) -> bool:
        """Apply one movement; False (and a recorded reason) when it carries nothing to apply."""
        code = m.code
        if code in CAPITAL_CODES:
            after, amount = m.value("capital_after_eur"), m.value("amount_eur")
            if after is None and amount is None:
                return self._skip(m, "mentions a capital change without its amount or resulting capital")
            if not amount and after is not None and state.capital_eur is not None and round(after) == round(state.capital_eur):
                return self._skip(m, f"capital unchanged at {after:,.0f} EUR (e.g. a par-value change)")
            if code == "CAPITAL_INCREASE" and None not in (after, amount, state.capital_eur) and round(after) == round(amount) == round(state.capital_eur):
                return self._skip(m, f"describes the incorporation capital of {after:,.0f} EUR, which is not an increase")

        if code == "CAPITAL_INCREASE":
            new_shares = self._capital(state, m, +1)
            share_class = m.value("share_class")
            subscribers = m.value("subscribers") or []
            allocated: int | None = 0
            for sub in subscribers:
                self.add(state, sub.name, sub.shares, kind=sub.kind, siren=sub.siren, share_class=sub.share_class or share_class)
                allocated = _plus(allocated, sub.shares)
            remaining = None if new_shares is None or allocated is None else new_shares - allocated
            named = state.named()
            if not remaining:
                return True
            if (
                m.value("method") == "incorporation de reserves"
                and named
                and len(named) == len(state.holdings)
                and all(h.shares for h in named.values())
            ):
                total = sum(h.shares for h in named.values())
                given = 0
                for h in sorted(named.values(), key=lambda h: -h.shares):
                    part = remaining * h.shares // total
                    h.shares += part
                    given += part
                max(named.values(), key=lambda h: h.shares).shares += remaining - given
                state.notes.append(f"{m.event_id}: {remaining} bonus shares allocated pro rata to existing holders")
            else:
                self.to_bucket(state, remaining, share_class, f"{remaining} new shares from {m.event_id}, subscriber not named")

        elif code == "CAPITAL_DECREASE":
            cancelled = self._capital(state, m, -1)
            exits = [x for x in same_day if x.code == "SHAREHOLDER_END" and x.value("shares")]
            named_total = sum(x.value("shares") for x in exits)
            bucket = next((k for k, h in state.holdings.items() if k.startswith("~") and h.shares == named_total), None)
            self._preferred_bucket = bucket
            residual = None if cancelled is None else cancelled - named_total
            if residual and not self.from_bucket(state, residual):
                state.checks.append(f"{m.event_id}: {residual} cancelled shares could not be attributed to any holder")

        elif code == "SHAREHOLDER_SHARE_TRANSFER":
            seller, buyer, shares = m.value("from_name"), m.value("to_name"), m.value("shares")
            if not seller and shares is None:
                return self._skip(m, "transfer with neither a named seller nor a share count")
            if not seller and not buyer:
                return self._skip(m, "transfer with no named party")
            moved = self.take(state, seller, shares, m.event_id) if seller else shares
            if not seller and moved is not None and not self.from_bucket(state, moved):
                state.notes.append(f"{m.event_id}: seller not named and no unidentified shares to cover {moved}")
            if buyer:
                if moved is None and self.find(state, buyer) is not None:
                    state.notes.append(f"{m.event_id}: number of shares {buyer} received is not stated; holding left unchanged")
                else:
                    self.add(state, buyer, moved)
            elif moved is not None:
                self.to_bucket(state, moved, None, f"{m.event_id}: buyer of {seller}'s shares not named")

        elif code == "SHAREHOLDER_ENTRY":
            holder = m.value("holder_name")
            if not holder:
                return self._skip(m, "entry without a holder name")
            if self.find(state, holder) is None:
                shares = m.value("shares")
                if shares is not None and self.from_bucket(state, shares):
                    state.notes.append(f"{m.event_id}: {holder}'s {shares} shares identified among previously unidentified holders")
                self.add(state, holder, shares, kind=m.value("holder_kind"), siren=m.value("holder_siren"))

        elif code == "SHAREHOLDER_END":
            holder = m.value("holder_name")
            if not holder:
                return self._skip(m, "exit without a holder name")
            decrease_today = any(x.code == "CAPITAL_DECREASE" for x in same_day)
            if decrease_today:
                self.take(state, holder, m.value("shares"), m.event_id)
            elif (key := self.find(state, holder)) is not None:
                gone = state.holdings.pop(key)
                if gone.shares:
                    self.to_bucket(state, gone.shares, gone.share_class, f"{m.event_id}: {gone.name} left; acquirer not identified")
            else:
                state.notes.append(f"{m.event_id}: exit of {holder}, who was never identified as a holder")

        elif code == "CAPITAL_DUAL_CLASS":
            state.notes.append(f"{m.event_id}: share class created: {m.value('share_class') or m.value('mechanism')}")
        return True

    # ---- snapshots ------------------------------------------------------------------------------------------

    def reconcile(self, state: State, refs: list[SnapshotRef]) -> None:
        # Complete holder lists first, so a same-day par-value change rescales the corrected holdings.
        for ref in sorted(refs, key=lambda r: (not r.snapshot.holders_complete, r.as_of)):
            snap = ref.snapshot
            if snap.capital_eur is not None and state.capital_eur is not None and round(snap.capital_eur) != round(state.capital_eur):
                self.unmatched.append(ref)
                continue
            state.capital_eur = state.capital_eur if state.capital_eur is not None else snap.capital_eur
            if snap.shares_total is not None:
                if state.shares_total is None:
                    state.shares_total = snap.shares_total
                elif snap.shares_total != state.shares_total:
                    ratio = snap.shares_total / state.shares_total
                    if ratio.is_integer() or (1 / ratio).is_integer():
                        for h in state.holdings.values():
                            h.shares = None if h.shares is None else round(h.shares * ratio)
                        state.notes.append(
                            f"share count {state.shares_total:,} -> {snap.shares_total:,} with capital unchanged "
                            f"(par value change) per {ref.label}"
                        )
                        state.shares_total = snap.shares_total
                        state.nominal_eur = snap.nominal_eur
                    else:
                        state.checks.append(f"{ref.label} states {snap.shares_total:,} shares; reconstructed {state.shares_total:,}")
            if snap.nominal_eur is not None and state.nominal_eur is None:
                state.nominal_eur = snap.nominal_eur
            if snap.holders_complete and snap.holders:
                self._reset_holders(state, ref)
            state.evidence.append(ref)

    def _reset_holders(self, state: State, ref: SnapshotRef) -> None:
        holders = ref.snapshot.holders
        if len(holders) == 1 and holders[0].shares is None:
            counts = [state.shares_total]
        elif all(h.shares is not None for h in holders):
            counts = [h.shares for h in holders]
        else:
            state.notes.append(f"{ref.label}: complete holder list without share counts; not used to reset holders")
            return
        new = {}
        for h, shares in zip(holders, counts):
            key = self.find(state, h.name) or normalize_name(h.name, self.aliases)
            old = state.holdings.get(key)
            name = old.name if old else h.name
            new[key] = Holding(name, h.kind if h.kind != "UNKNOWN" else (old.kind if old else guess_kind(name)),
                               h.siren or (old.siren if old else None), shares, h.share_class)
        before = {k: h.shares for k, h in state.holdings.items()}
        after = {k: h.shares for k, h in new.items()}
        if before != after:
            changes, lost, gained = [], {}, {}
            for key in sorted(before.keys() | after.keys()):
                label = (new.get(key) or state.holdings.get(key)).name
                old_n, new_n = before.get(key) or 0, after.get(key) or 0
                if before.get(key) != after.get(key):
                    changes.append(f"{label} {before.get(key, 0)} -> {after.get(key, 0)}")
                (lost if new_n < old_n else gained)[key] = abs(new_n - old_n)
            if lost and all(k.startswith("~") for k in lost) and sum(lost.values()) == sum(gained.values()):
                # The list only names who held shares the reconstruction could not attribute: not a disagreement.
                state.notes.append(f"unidentified shares attributed per the complete list in {ref.label}: " + "; ".join(changes))
            else:
                state.checks.append(f"holders reset to the complete list in {ref.label}: " + "; ".join(changes))
        state.holdings = new

    # ---- derived entries / exits ----------------------------------------------------------------------------

    def derive(self, before: State, after: State, movements: list[Movement], grounding: Grounding, date: str) -> list[DerivedEvent]:
        ids = [m.event_id for m in movements]
        explicit = [(m.code, m.value("holder_name")) for m in movements if m.code in ("SHAREHOLDER_ENTRY", "SHAREHOLDER_END")]
        transfers = [m for m in movements if m.code == "SHAREHOLDER_SHARE_TRANSFER"]
        old, new = before.named(), after.named()
        entered = [new[k] for k in new.keys() - old.keys()]
        left = [old[k] for k in old.keys() - new.keys()]
        out = []
        for h in entered:
            if not any(code == "SHAREHOLDER_ENTRY" and same_party(n, h.name, self.aliases) for code, n in explicit):
                out.append(DerivedEvent("SHAREHOLDER_ENTRY", date, grounding,
                                        {"holder_name": h.name, "holder_siren": h.siren, "shares": h.shares},
                                        "derived: holder appears in the cap table after this date's movements", ids))
        for h in left:
            if not any(code == "SHAREHOLDER_END" and same_party(n, h.name, self.aliases) for code, n in explicit):
                out.append(DerivedEvent("SHAREHOLDER_END", date, grounding,
                                        {"holder_name": h.name, "holder_siren": h.siren, "shares": h.shares},
                                        "derived: holder no longer in the cap table after this date", ids))
        if not movements and not transfers:
            # Compare on the later share basis, in case the same reconciliation also changed the par value.
            ratio = 1
            if before.shares_total and after.shares_total and before.capital_eur == after.capital_eur:
                ratio = after.shares_total / before.shares_total
            old_n = {k: None if h.shares is None else round(h.shares * ratio) for k, h in old.items()}
            new_n = {k: h.shares for k, h in new.items()}
            losers = [(k, n - (new_n.get(k) or 0)) for k, n in old_n.items() if n and (new_n.get(k) or 0) < n]
            gainers = [(k, n - (old_n.get(k) or 0)) for k, n in new_n.items() if n and (old_n.get(k) or 0) < n]
            # Only an undocumented exit: a holder who drops out while exactly one other gains the same stake.
            # Partial disagreements between two holder lists stay checks, not invented transfers.
            if len(losers) == 1 and len(gainers) == 1 and losers[0][1] == gainers[0][1] and losers[0][0] not in new:
                (seller_key, delta), (buyer_key, _) = losers[0], gainers[0]
                # Report the count as it stood when the shares moved, i.e. before any par-value change.
                out.append(DerivedEvent("SHAREHOLDER_SHARE_TRANSFER", date, grounding,
                                        {"from_name": old[seller_key].name, "to_name": new[buyer_key].name, "shares": round(delta / ratio)},
                                        "derived: no document records this transfer; it is implied by two consecutive "
                                        "cap tables, and the date is the latest possible one", ids))
        return out

    # ---- fold -----------------------------------------------------------------------------------------------

    def _emit(self, state: State, before: State, date: str, movements: list[Movement], grounding: Grounding | None) -> State:
        out = state.clone()
        out.as_of = date
        out.caused_by = [m.event_id for m in movements]
        out.checks, out.notes, out.evidence = state.checks, state.notes, state.evidence
        if grounding is not None:
            out.derived = self.derive(before, state, movements, grounding, date)
        out.checks += invariant_checks(out)
        state.checks, state.notes, state.evidence = [], [], []
        self.states.append(out)
        return out

    def fold(self, movements: list[Movement], snapshots: list[SnapshotRef]) -> list[State]:
        moves_by_date: dict[str, list[Movement]] = defaultdict(list)
        for m in movements:
            if not m.confirmed:
                self.issues.append(f"{m.event_id} {m.code} on {m.effective_date}: decided but never shown carried out; left out of the timeline")
            elif not m.effective_date:
                self.issues.append(f"{m.event_id} {m.code}: no effective date; left out of the timeline")
            else:
                moves_by_date[m.effective_date].append(m)
        snaps_by_date: dict[str, list[SnapshotRef]] = defaultdict(list)
        for ref in snapshots:
            snaps_by_date[ref.as_of].append(ref)

        state = State()
        for date in sorted(moves_by_date.keys() | snaps_by_date.keys()):
            moves = sorted(moves_by_date.get(date, []), key=lambda m: (_ORDER[m.code], m.value("capital_after_eur") or 0))
            snaps = snaps_by_date.get(date, [])
            capital_moves = [m for m in moves if m.code in CAPITAL_CODES]

            # Snapshots taken as the meeting opens (no capital, or the pre-decision capital) describe the state before today's decisions.
            pre = [s for s in snaps if capital_moves and (s.snapshot.capital_eur is None or s.snapshot.capital_eur == state.capital_eur)]
            if pre:
                before = state.clone()
                self.reconcile(state, pre)
                if state.fingerprint() != before.fingerprint():
                    self._emit(state, before, date, [], pre[0].grounding)

            before, pending = state.clone(), []
            for m in moves:
                if not self.apply(state, m, moves):
                    continue
                pending.append(m)
                if m.code in CAPITAL_CODES and m is not capital_moves[-1]:
                    self._emit(state, before, date, pending, pending[0].primary.grounding)
                    before, pending = state.clone(), []
            self._preferred_bucket = None

            post = [s for s in snaps if s not in pre]
            self.reconcile(state, post)
            if pending or state.fingerprint() != before.fingerprint():
                grounding = pending[0].primary.grounding if pending else (post[0].grounding if post else None)
                self._emit(state, before, date, pending, grounding)
            elif self.states and (state.checks or state.notes):
                self.states[-1].checks += state.checks
                self.states[-1].notes += state.notes
                state.checks, state.notes = [], []

        for ref in self.unmatched:
            self.issues.append(
                f"{ref.label} states capital {ref.snapshot.capital_eur:,.0f} as of {ref.as_of}, "
                "which matches no reconstructed state at that date"
            )
        return self.states


def invariant_checks(state: State) -> list[str]:
    out = []
    if state.shares_total is not None and state.shares_total < 0:
        out.append(f"share count is negative ({state.shares_total:,}): a movement earlier in the chain is missing")
    if None not in (state.capital_eur, state.shares_total, state.nominal_eur):
        if abs(state.capital_eur - state.shares_total * state.nominal_eur) > 0.5:
            out.append(f"capital {state.capital_eur:,.0f} != {state.shares_total:,} shares x {state.nominal_eur} nominal")
    counts = [h.shares for h in state.holdings.values()]
    if state.shares_total is not None and counts and None not in counts and sum(counts) != state.shares_total:
        out.append(f"holders hold {sum(counts):,} shares; total is {state.shares_total:,}")
    return out
