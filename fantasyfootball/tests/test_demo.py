"""The demo league: the guest endpoint, starting the draft by hand, guest limits, cleanup, and the rate limit."""
from datetime import timedelta

import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from accounts.models import User
from fantasy import demo
from fantasy.demo import create_demo, delete_old_guests
from fantasy.draft import run_league, start_draft, team_on_clock
from fantasy.models import DraftPick, League, RosterEntry

from .conftest import NOW


@pytest.fixture(autouse=True)
def small_demo(monkeypatch):
    """The fake NFL can't fill 10 rosters, so demos here have 4 teams. Also forget earlier tests' rate-limit counts."""
    monkeypatch.setattr(demo, "DEMO_SIZE", 4)
    cache.clear()


def guest_client(nfl) -> tuple[APIClient, dict]:
    """POST /demo/ with no login, then a client that sends the new guest's access token."""
    response = APIClient().post("/api/fantasy/demo/")
    assert response.status_code == 201
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")
    return client, response.data


def test_demo_makes_a_guest_league(nfl):
    """A guest who commissions a SETUP demo league: their team plus bots."""
    _, data = guest_client(nfl)
    league = League.objects.get(pk=data["league_id"])
    assert league.is_demo and league.status == League.Status.SETUP
    assert league.commissioner.is_guest
    assert league.teams.count() == 4
    assert league.teams.filter(owner=None).count() == 3
    assert league.teams.get(owner__isnull=False).id == data["team_id"]


def test_start_runs_bots_until_the_guest_is_up(nfl):
    """draft/start: the draft is live, every bot ahead of the guest has picked, and the guest is on the clock."""
    client, data = guest_client(nfl)
    response = client.post(f"/api/fantasy/leagues/{data['league_id']}/draft/start/")
    assert response.status_code == 200
    league = League.objects.get(pk=data["league_id"])
    guest_team = league.teams.get(pk=data["team_id"])
    assert league.status == League.Status.DRAFTING
    assert team_on_clock(league) == guest_team
    assert DraftPick.objects.filter(league=league).count() == guest_team.draft_position - 1


def test_only_demo_leagues_start_by_hand(league, api):
    """A normal league still waits for its draft_time."""
    league.league.status = League.Status.SETUP
    league.league.save()
    assert api(league.me_user).post(f"/api/fantasy/leagues/{league.league.id}/draft/start/").status_code == 400


def test_guests_stay_in_their_demo(nfl):
    """A guest can't create a real league or join one."""
    client, _ = guest_client(nfl)
    assert client.post("/api/fantasy/leagues/", {"name": "Real", "team_name": "Mine"}, format="json").status_code == 403
    assert client.post("/api/fantasy/leagues/join/", {"invite_code": "ABCD1234", "team_name": "Mine"}, format="json").status_code == 403


def test_offseason_has_no_demo(nfl):
    """After the regular season (week 16+), the endpoint says no."""
    nfl.season.current_week = 16
    nfl.season.save()
    assert APIClient().post("/api/fantasy/demo/").status_code == 400


def test_old_guests_are_deleted(nfl, league):
    """A drafted demo is gone after 7 days, with its guest. Real users and leagues are untouched."""
    guest, demo_league, team = create_demo()
    team.autodraft = True
    team.save()
    start_draft(demo_league)
    run_league(demo_league)
    assert RosterEntry.objects.filter(league=demo_league).exists()

    assert delete_old_guests(NOW + timedelta(days=6)) == 0
    assert delete_old_guests(NOW + timedelta(days=8)) == 1
    assert not User.objects.filter(pk=guest.pk).exists()
    assert not League.objects.filter(pk=demo_league.pk).exists()
    assert League.objects.filter(pk=league.league.pk).exists()


def test_five_demos_an_hour(nfl):
    """The 6th demo from one address in an hour gets 429 Too Many Requests."""
    codes = [APIClient().post("/api/fantasy/demo/").status_code for _ in range(6)]
    assert codes == [201] * 5 + [429]
