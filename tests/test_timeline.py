"""The reconstructed ARCHEAN chain, as synthetic extractions: exercises reconciliation, buckets, splits, derived events."""

from pathlib import Path

from takeovers_challenge.consolidate import Candidate, Grounding, Movement
from takeovers_challenge.corpus import Document
from takeovers_challenge.schemas import CapitalSnapshot, Evidence, ExtractedEvent, HolderStake
from takeovers_challenge.timeline import Folder, SnapshotRef

DOC = Document("0" * 24, "2020-01-01", {}, Path(), Path(), [])
G = Grounding(DOC.inpi_id, 1, [0, 0, 1, 1], "", [], 100, True)
_n = iter(range(1, 1000))


def mv(code, date, **fields) -> Movement:
    base = {name: None for name in ExtractedEvent.model_fields}
    base.update(code=code, status="completed_here", effective_date=date, date_basis="", subscribers=[],
                evidence=Evidence(line_ids=[], quote_fr=""), gloss_en="", confidence="high")
    base.update(fields)
    return Movement([Candidate(DOC, ExtractedEvent(**base), G)], event_id=f"ev{next(_n):03d}")


def snap(as_of, capital=None, shares=None, nominal=None, holders=(), complete=False) -> SnapshotRef:
    stakes = [HolderStake(name=n, kind="UNKNOWN", siren=None, shares=s, share_class=None, pct=None) for n, s in holders]
    s = CapitalSnapshot(as_of=as_of, as_of_basis="", capital_eur=capital, shares_total=shares, nominal_eur=nominal,
                        holders=stakes, holders_complete=complete, evidence=Evidence(line_ids=[], quote_fr=""))
    return SnapshotRef(DOC, s, G)


def holders(state):
    return {h.name: h.shares for h in state.holdings.values()}


def test_archean_chain():
    sub = lambda n, s: HolderStake(name=n, kind="PERSON", siren=None, shares=s, share_class=None, pct=None)  # noqa: E731
    movements = [
        mv("CAPITAL_INCREASE", "2005-05-17", amount_eur=113000, capital_after_eur=150000, shares_delta=1130, method="numeraire"),
        mv("SHAREHOLDER_END", "2005-08-16", holder_name="Malik GUELLATI"),
        mv("CAPITAL_INCREASE", "2006-10-20", amount_eur=50000, capital_after_eur=200000, shares_delta=500, method="numeraire",
           subscribers=[sub("Xavier AUMONT", 330), sub("Antonio BLANCO MARINA", 150), sub("Franck GICQUEL", 20)]),
        mv("SHAREHOLDER_SHARE_TRANSFER", "2006-10-20", from_name="Xavier AUMONT", to_name="Michel CAPGRAS", shares=225),
        mv("SHAREHOLDER_SHARE_TRANSFER", "2007-09-07", from_name="Xavier AUMONT", to_name="HADEAN", shares=742),
        mv("SHAREHOLDER_SHARE_TRANSFER", "2007-09-07", from_name="Franck GICQUEL", to_name="HADEAN", shares=80),
        mv("SHAREHOLDER_SHARE_TRANSFER", "2008-04-30", from_name="Michel CAPGRAS", to_name="HADEAN", shares=225),
        mv("CAPITAL_INCREASE", "2008-06-27", amount_eur=17241, capital_after_eur=217241, shares_delta=17241, share_class="A", nominal_eur=1),
        mv("CAPITAL_INCREASE", "2008-06-27", amount_eur=150861, capital_after_eur=368102, shares_delta=150861, share_class="B", nominal_eur=1),
        mv("CAPITAL_DECREASE", "2017-02-21", amount_eur=150861, capital_after_eur=217241, shares_delta=150861, method="autre"),
        *[mv("SHAREHOLDER_END", "2017-02-21", holder_name=n, shares=s)
          for n, s in [("FPCI SECURITE", 64655), ("FIP GALIA PME 4", 12931), ("GALIA VENTURE", 30172), ("FPCI FINANCIERE DE BRIENNE", 43103)]],
        mv("CAPITAL_INCREASE", "2018-03-23", amount_eur=182759, capital_after_eur=400000, shares_delta=182759, method="incorporation de reserves"),
    ]
    snapshots = [
        snap("2004-12-15", 37000, 370, 100, [("Xavier AUMONT", 155), ("Antonio BLANCO MARINA", 155), ("Franck GICQUEL", 60)], True),
        snap("2005-08-16", None, None, None, [("Antonio BLANCO", 823), ("Xavier AUMONT", 617), ("Franck GICQUEL", 60)], True),
        snap("2006-10-20", 150000, 1500, None, [("Antonio BLANCO", 803), ("Xavier AUMONT", 637), ("Franck GICQUEL", 60)], True),
        snap("2008-06-27", None, None, None, [("HADEAN", None)], True),
        snap("2008-06-27", 200000, 200000, 1),
        snap("2017-02-21", 217241, 217241, 1, [("HADEAN", 217241)], True),
    ]
    folder = Folder()
    states = folder.fold(movements, snapshots)
    by_date = {}
    for s in states:
        by_date.setdefault(s.as_of, []).append(s)

    assert holders(by_date["2004-12-15"][-1]) == {"Xavier AUMONT": 155, "Antonio BLANCO MARINA": 155, "Franck GICQUEL": 60}
    assert holders(by_date["2005-05-17"][-1])["Unidentified holders"] == 1130
    assert holders(by_date["2005-08-16"][-1]) == {"Antonio BLANCO MARINA": 823, "Xavier AUMONT": 617, "Franck GICQUEL": 60}

    # The attendance sheet disagrees with the 2005 répartition: recorded as a check, and the later list wins.
    oct_pre, oct_post = by_date["2006-10-20"]
    assert any("823 -> 803" in c for c in oct_pre.checks)
    assert not oct_pre.derived  # a 20-share disagreement between two lists is a check, not an invented transfer
    assert holders(oct_post) == {"Antonio BLANCO MARINA": 953, "Xavier AUMONT": 742, "Franck GICQUEL": 80, "Michel CAPGRAS": 225}
    assert not oct_post.checks

    sep = by_date["2007-09-07"][-1]
    assert {(d.code, d.payload["holder_name"]) for d in sep.derived} >= {
        ("SHAREHOLDER_ENTRY", "HADEAN"), ("SHAREHOLDER_END", "Xavier AUMONT"), ("SHAREHOLDER_END", "Franck GICQUEL")}

    # HADEAN as sole shareholder with no recorded BLANCO transfer: a derived, flagged transfer.
    june = by_date["2008-06-27"]
    assert any(d.code == "SHAREHOLDER_SHARE_TRANSFER" and d.payload["from_name"] == "Antonio BLANCO MARINA"
               and d.payload["shares"] == 953 for d in june[0].derived)
    last_june = june[-1]
    assert last_june.capital_eur == 368102 and last_june.shares_total == 368102
    assert holders(last_june) == {"HADEAN": 200000, "Unidentified holders (A shares)": 17241, "Unidentified holders (B shares)": 150861}
    assert [s.capital_eur for s in june] == [200000, 217241, 368102]

    feb = by_date["2017-02-21"][-1]
    assert holders(feb) == {"HADEAN": 217241} and not any("cannot" in c for c in feb.checks)

    final = by_date["2018-03-23"][-1]
    assert holders(final) == {"HADEAN": 400000} and final.shares_total == 400000 and not final.checks


def test_mentions_without_substance_are_skipped_not_applied():
    """Regressions found by scoring model output against the hand labels."""
    movements = [
        # A later statute recalling "the increase of 17 May 2005" with no figures must not wipe the share count.
        mv("CAPITAL_INCREASE", "2005-05-17", status="restated_history"),
        # A transfer of unknown size to an existing holder must not erase that holder's known count.
        mv("SHAREHOLDER_SHARE_TRANSFER", "2005-08-16", to_name="Xavier AUMONT"),
        mv("SHAREHOLDER_SHARE_TRANSFER", "2005-08-16", from_name="Malik GUELLATI", to_name="Xavier AUMONT"),
        # The incorporation capital restated as an "increase", and a par-value split coded as a decrease.
        mv("CAPITAL_INCREASE", "2005-09-01", amount_eur=37000, capital_after_eur=37000),
        mv("CAPITAL_DECREASE", "2005-09-02", amount_eur=0, capital_after_eur=37000, shares_delta=36630),
    ]
    snapshots = [snap("2004-12-15", 37000, 370, 100, [("Xavier AUMONT", 155), ("Franck GICQUEL", 215)], True)]
    folder = Folder()
    states = folder.fold(movements, snapshots)

    last = states[-1]
    assert last.capital_eur == 37000 and last.shares_total == 370
    assert holders(last) == {"Xavier AUMONT": 155, "Franck GICQUEL": 215}
    assert set(folder.skipped) == {movements[0].event_id, movements[1].event_id, movements[3].event_id, movements[4].event_id}
    assert all(s.as_of == "2004-12-15" or s.caused_by for s in states)


def test_share_classes_match_on_their_letters():
    from takeovers_challenge.consolidate import _same_movement

    a = mv("CAPITAL_DUAL_CLASS", "2008-06-27", share_class="Actions de préférence A").primary.event
    b = mv("CAPITAL_DUAL_CLASS", "2008-06-27", share_class="Actions de préférence B et B'").primary.event
    a2 = mv("CAPITAL_DUAL_CLASS", "2008-06-27", share_class="A").primary.event
    assert not _same_movement(a, b, {})
    assert _same_movement(a, a2, {})
