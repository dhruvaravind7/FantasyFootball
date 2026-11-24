from datetime import datetime, timedelta
from secrets import token_hex

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from accounts.models import User
from league.models import Season

from .models import PLAYOFF_LAST_WEEK, FantasyTeam, League, ScoringRules

DEMO_SIZE = 10
GUEST_DAYS = 7

@transaction.atomic
def create_demo() -> tuple[User, League, FantasyTeam]:
    """A new guest account, commissioner of a new 10-team demo league: the guest's team plus 9 bots. The draft waits."""
    # 1. Only during the regular season
    season = Season.objects.filter(active=True).first()
    if season is None or season.current_week is None or season.current_week > PLAYOFF_LAST_WEEK - 2:
        raise ValidationError({"detail": "The demo opens during the NFL regular season."})

    # 2. The guest: a random username, a matching .invalid email, no password, is_guest on
    username = f"guest-{token_hex(6)}"
    guest = User(username=username, email=f"{username}@guest.invalid", is_guest=True)
    guest.set_unusable_password()
    guest.save()
    # 3. The league (the guest commissions it), its scoring rules, and the guest's team
    league = League.objects.create(name="Demo League", season=season, commissioner=guest, size=DEMO_SIZE, is_demo=True)
    team = league.teams.create(name="Your Team", owner=guest)
    ScoringRules.objects.create(league=league)
    # 4. Bots "Bot 1" ... "Bot 9" in one query (a bot is a team with no owner)
    FantasyTeam.objects.bulk_create([FantasyTeam(league=league, owner=None, name=f"Bot {n}") for n in range(1, DEMO_SIZE)])
    return guest, league, team

def delete_old_guests(now: datetime | None = None) -> int:
    """Delete guests older than GUEST_DAYS, and the leagues they run first (commissioner is PROTECT). Returns guests deleted."""
    now = now or timezone.now()
    old = User.objects.filter(is_guest=True, date_joined__lt=now - timedelta(days=GUEST_DAYS))
    League.objects.filter(commissioner__in=old).delete()                   # 1. their leagues (and everything in them)
    _, deleted = old.delete()                                              # 2. then the guests
    return deleted.get("accounts.User", 0)
