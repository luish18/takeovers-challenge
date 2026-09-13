"""What the model returns for one document (as the input of the `record_extraction` tool). Unstated fields may be omitted."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

EventCode = Literal[
    "CAPITAL_INCREASE",
    "CAPITAL_DECREASE",
    "SHAREHOLDER_ENTRY",
    "SHAREHOLDER_END",
    "SHAREHOLDER_SHARE_TRANSFER",
    "CAPITAL_DUAL_CLASS",
]

# How this document relates to the movement. Only the first three change the cap table at `effective_date`.
EventStatus = Literal[
    "completed_here",  # decided and effective in this act
    "realisation_of_earlier_decision",  # this act records that an earlier authorisation was carried out
    "decided_pending_realisation",  # decided here, but subject to subscriptions/conditions not confirmed in this act
    "restated_history",  # recalled from the past, e.g. the running history in statutes article "Apports"
    "authorisation_only",  # a power granted (e.g. delegation to the president), nothing happened yet
    "rejected",
]

Method = Literal["numeraire", "incorporation de reserves", "apport en nature", "autre"]
HolderKind = Literal["PERSON", "COMPANY", "UNKNOWN"]
Confidence = Literal["high", "medium", "low"]


class Evidence(BaseModel):
    line_ids: list[str] = Field(description="Ids of the OCR lines that state this, e.g. ['p3.5', 'p3.6']. Fewest lines that suffice.")
    quote_fr: str = Field(description="Verbatim French text of those lines, OCR errors included.")


class HolderStake(BaseModel):
    name: str
    kind: HolderKind = "UNKNOWN"
    siren: str | None = None
    shares: int | None = None
    share_class: str | None = None
    pct: float | None = None


class ExtractedEvent(BaseModel):
    code: EventCode
    status: EventStatus
    effective_date: str | None = Field(None, description="YYYY-MM-DD when the movement took effect; null if the document does not say.")
    date_basis: str = Field("", description="One sentence: where the date comes from (meeting date, realisation date, transfer order date...).")
    amount_eur: float | None = Field(None, description="Nominal change of capital in euros (capital events).")
    capital_before_eur: float | None = None
    capital_after_eur: float | None = None
    shares_delta: int | None = Field(None, description="Number of shares created (increase) or cancelled (decrease).")
    nominal_eur: float | None = Field(None, description="Par value per share after the event, if stated.")
    method: Method | None = Field(None, description="Capital events only.")
    mechanism: str | None = Field(None, description="Free text: e.g. 'compensation de créances', 'rachat et annulation', 'actions de préférence A'.")
    share_class: str | None = None
    holder_name: str | None = Field(None, description="SHAREHOLDER_ENTRY / SHAREHOLDER_END: who joins or leaves.")
    holder_kind: HolderKind | None = None
    holder_siren: str | None = Field(None, description="Only if printed in the document.")
    from_name: str | None = Field(None, description="SHAREHOLDER_SHARE_TRANSFER: seller / cédant.")
    to_name: str | None = Field(None, description="SHAREHOLDER_SHARE_TRANSFER: buyer / cessionnaire.")
    shares: int | None = Field(None, description="Shares moved, or held by the entering/leaving holder.")
    price_per_share_eur: float | None = None
    subscribers: list[HolderStake] = Field(default_factory=list, description="CAPITAL_INCREASE: who received the new shares and how many, if stated.")
    evidence: Evidence
    gloss_en: str = Field("", description="One-sentence English translation/summary of the evidence.")
    confidence: Confidence = "medium"
    notes: str | None = None


class CapitalSnapshot(BaseModel):
    """A statement of the cap table at a point in time (statutes article 'Capital social', a répartition table...)."""

    as_of: str | None = Field(None, description="YYYY-MM-DD the state applies to; null if only 'as of this document'.")
    as_of_basis: str = ""
    capital_eur: float | None = None
    shares_total: int | None = None
    nominal_eur: float | None = None
    holders: list[HolderStake] = Field(default_factory=list)
    holders_complete: bool = Field(False, description="True only if the document presents this holder list as the full cap table.")
    evidence: Evidence


class DecisionDate(BaseModel):
    date: str = Field(description="YYYY-MM-DD")
    body: str = Field(description="e.g. 'AGE', 'associé unique', 'président', 'constitution'.")
    evidence: Evidence


class DocExtraction(BaseModel):
    summary_en: str = Field(description="Two or three sentences: what this filing is and what, if anything, it does to the capital.")
    decision_dates: list[DecisionDate] = Field(default_factory=list)
    events: list[ExtractedEvent] = Field(default_factory=list)
    snapshots: list[CapitalSnapshot] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list, description="Contradictions, OCR doubts, or things you could not resolve in this document.")
