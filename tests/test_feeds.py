"""Feed identity, local dates, status mapping, and the C1 matching case."""
import datetime as dt
import pytest
from feeds import (SQL_STATS_API, espn_status, et_date, feed_of, parse_utc,
                   slot_or_none, stats_api_status)


def test_feed_is_identified_by_id_shape():
    assert feed_of("mlb-823494") == "statsapi"
    assert feed_of("mlb-espn-401817028") == "espn"
    # Odds ids are hex and often START WITH A DIGIT. A GLOB 'mlb-[0-9]*' test
    # calls this one statsapi, which counted 23 scheduled games where there
    # were 15.
    assert feed_of("mlb-85b75fcab1b69e0a4e414382") == "oddsfeed"


def test_a_late_game_belongs_to_the_previous_local_date():
    # THE C1 CASE. SD @ LAD, first pitch 2026-09-23T02:11Z, which is Tuesday
    # the 22nd at 10:11pm ET. Dating it 09-23 is what handed Wednesday's game
    # Tuesday's prices.
    assert et_date("2026-09-23T02:11:00Z") == "2026-09-22"
    assert et_date("2026-09-24T02:11:00Z") == "2026-09-23"


def test_an_afternoon_game_keeps_its_own_date():
    assert et_date("2026-09-22T17:05:00Z") == "2026-09-22"


def test_parse_utc_accepts_every_format_the_feeds_use():
    for v in ("2026-09-22T22:41:00Z", "2026-09-22T22:41:00+00:00",
              "2026-09-22T22:41:00"):
        assert parse_utc(v).tzinfo is not None
    assert parse_utc(None) is None
    assert parse_utc("not a time") is None


@pytest.mark.parametrize("detail,abstract,expected", [
    ("Final", "Final", "final"),
    ("Game Over", "Final", "final"),
    # THE C3 CASE. abstractGameState says "Final" while detailedState says
    # "Postponed", and reading the abstract one made postponements permanent.
    ("Postponed", "Final", "postponed"),
    ("Suspended", "Final", "suspended"),
    ("Cancelled", "Final", "cancelled"),
    ("In Progress", "Live", "live"),
    ("Scheduled", "Preview", "scheduled"),
])
def test_stats_api_status_reads_the_detailed_field(detail, abstract, expected):
    assert stats_api_status({"detailedState": detail,
                             "abstractGameState": abstract}) == expected


@pytest.mark.parametrize("name,state,expected", [
    ("STATUS_FINAL", "post", "final"),
    ("STATUS_POSTPONED", "post", "postponed"),
    ("STATUS_CANCELED", "post", "cancelled"),
    ("STATUS_IN_PROGRESS", "in", "live"),
    ("STATUS_SCHEDULED", "pre", "scheduled"),
])
def test_espn_status_reads_the_detailed_name(name, state, expected):
    assert espn_status({"name": name, "state": state}) == expected


def test_an_unknown_espn_status_never_invents_a_final_from_nothing():
    # Falling back to the coarse state is fine; inventing a completion is not.
    assert espn_status({"name": "STATUS_BRAND_NEW", "state": "pre"}) == "scheduled"
    assert espn_status({}) == "scheduled"


def test_stats_api_predicate_excludes_odds_ids():
    assert "NOT GLOB" in SQL_STATS_API


def test_feed_of_is_sport_agnostic():
    # 'nfl-espn-401872947' was classified as oddsfeed because the check
    # hard-coded the 'mlb-' prefix.
    assert feed_of("nfl-espn-401872947") == "espn"
    assert feed_of("nfl-espn-401872947") == feed_of("mlb-espn-401817028")
    assert feed_of("nfl-823494") == "statsapi"
