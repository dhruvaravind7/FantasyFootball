"""Fixes from the full-codebase review: rate limits, permissions, input checks, roster limits, and sync safety."""
from datetime import timedelta

import pytest
from django.core.cache import cache
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from accounts.models import User
from fantasy import demo
from fantasy.draft import autopick, start_draft, team_on_clock
from fantasy.models import League, RosterEntry, ScoringRules, WaiverHold
from fantasy.waiver_clock import clear_expired_holds
from league import sync
from league.espn import ESPNFormatError
from league.models import Player

from .conftest import NOW, WEEK


@pytest.fixture(autouse=True)
def fresh_rate_limits():
    """Throttle counts live in the cache, which survives between tests."""
    cache.clear()


@pytest.fixture
def setup_league(nfl, api):
    """A league still in SETUP, made through the API by a new user. Returns (league, that user's client)."""
    user = User.objects.create_user(email="boss@x.com", username="boss", password="pw")
    client = api(user)
    response = client.post("/api/fantasy/leagues/", {"name": "Setup", "team_name": "Boss", "size": 4}, format="json")
    assert response.status_code == 201
    return League.objects.get(pk=response.data["id"]), client


def test_fake_forwarded_for_is_ignored(nfl, monkeypatch):
    """A made-up X-Forwarded-For on every request no longer looks like a new address each time."""
    monkeypatch.setattr(demo, "DEMO_SIZE", 4)
    codes = [APIClient(HTTP_X_FORWARDED_FOR=f"10.0.0.{i}").post("/api/fantasy/demo/").status_code for i in range(6)]
    assert codes == [201] * 5 + [429]


def test_commissioner_cant_propose_for_another_team(league, api):
    """The commissioner can see another team's open trades, but not propose one from that team."""
    url = f"/api/fantasy/teams/{league.you.id}/trades/"
    mine = league.me.roster.filter(week=WEEK, slot=RosterEntry.Slot.BENCH).first()
    theirs = league.you.roster.filter(week=WEEK, slot=RosterEntry.Slot.BENCH).first()
    body = {"receiver_id": league.me.id, "send_entry_ids": [theirs.id], "receive_entry_ids": [mine.id]}
    assert api(league.me_user).post(url, body, format="json").status_code == 403
    assert api(league.me_user).get(url).status_code == 200


@pytest.mark.parametrize("path, field", [("queue/remove", "entry_id"), ("claims/cancel", "claim_id")])
def test_text_ids_are_a_400(league, api, path: str, field: str):
    """A non-number id is a bad request, not a server crash."""
    response = api(league.me_user).post(f"/api/fantasy/teams/{league.me.id}/{path}/", {field: "abc"}, format="json")
    assert response.status_code == 400
    assert field in response.data


@pytest.mark.parametrize("change", [
    {"bench_slots": 500},                                                              # one field out of range
    {"bench_slots": 10, "qb_slots": 3, "rb_slots": 3, "wr_slots": 3, "te_slots": 3,
     "flex_slots": 3, "dst_slots": 3, "k_slots": 3},                                   # 21 starters + 10 bench = 31, over the 29 the limits allow
    {"qb_slots": 0, "rb_slots": 0, "wr_slots": 0, "te_slots": 0, "flex_slots": 0, "dst_slots": 0, "k_slots": 0},   # no starters
])
def test_roster_slots_are_limited(setup_league, change: dict):
    """Slot counts that would make a draft impossible are rejected."""
    lg, client = setup_league
    assert client.patch(f"/api/fantasy/leagues/{lg.id}/", change, format="json").status_code == 400


def test_free_agents_during_the_draft_hide_drafted_players(setup_league):
    """Mid-draft, the free-agent list leaves out everyone already picked."""
    lg, client = setup_league
    start_draft(lg)
    for _ in range(3):
        lg.refresh_from_db()
        autopick(lg, team_on_clock(lg))
    drafted = set(lg.picks.exclude(player=None).values_list("player_id", flat=True))
    listed = {row["player"]["id"] for row in client.get(f"/api/fantasy/leagues/{lg.id}/free-agents/").data if row["player"]}
    assert drafted and not drafted & listed


def test_join_is_rate_limited(nfl, api):
    """Guessing invite codes stops after 10 tries an hour."""
    client = api(User.objects.create_user(email="guess@x.com", username="guess", password="pw"))
    codes = [client.post("/api/fantasy/leagues/join/", {"invite_code": f"ZZZZZ{i}", "team_name": "G"}, format="json").status_code
             for i in range(11)]
    assert codes == [404] * 10 + [429]


def test_password_too_similar_to_email(db):
    """Django's "too similar" rule now sees the new user's email and username."""
    client = APIClient()
    similar = client.post("/api/auth/register/", {"email": "dhruvfootball@x.com", "username": "df", "password": "dhruvfootball"}, format="json")
    assert similar.status_code == 400 and "password" in similar.data
    fine = client.post("/api/auth/register/", {"email": "dhruvfootball@x.com", "username": "df", "password": "Tangerine-Rocket-58"}, format="json")
    assert fine.status_code == 201


def test_short_injury_report_changes_nothing(db, monkeypatch):
    """An empty report from ESPN raises instead of marking every injured player healthy."""
    hurt = Player.objects.create(first_name="Hurt", last_name="Guy", position="RB", injury_status=Player.InjuryStatus.OUT)
    monkeypatch.setattr(sync.espn, "fetch_injuries", lambda: [])
    with pytest.raises(ESPNFormatError):
        sync.sync_injuries()
    hurt.refresh_from_db()
    assert hurt.injury_status == Player.InjuryStatus.OUT


def test_no_draft_after_the_regular_season(nfl):
    """In week 16 there's no regular season left to schedule, so the draft won't start."""
    nfl.season.current_week = 16
    nfl.season.save()
    owner = User.objects.create_user(email="late@x.com", username="late", password="pw")
    lg = League.objects.create(name="Late", season=nfl.season, commissioner=owner, size=4)
    ScoringRules.objects.create(league=lg)
    lg.teams.create(name="Late", owner=owner)
    with pytest.raises(ValidationError, match="too late"):
        start_draft(lg)


def test_old_waiver_holds_are_cleared(league):
    """Holds that cleared over a day ago are deleted; current ones stay."""
    old, current = [p for p in Player.objects.exclude(rosters__league=league.league)[:2]]
    WaiverHold.objects.create(league=league.league, player=old, clears_at=NOW - timedelta(days=2))
    WaiverHold.objects.create(league=league.league, player=current, clears_at=NOW + timedelta(days=1))
    assert clear_expired_holds() == 1
    assert list(WaiverHold.objects.values_list("player_id", flat=True)) == [current.id]
