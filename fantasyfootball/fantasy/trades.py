from collections import Counter
from datetime import timedelta, datetime

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .draft import POSITION_LIMITS
from .free_agency import current_entries
from .lineups import entry_position, entry_nfl_team_id, started_team_ids
from .models import FantasyTeam, League, RosterEntry, Trade, TradeItem, WaiverClaim
from .waiver_clock import start_hold
from .waivers import first_error

PUBLIC_STATUSES = [Trade.Status.ACCEPTED, Trade.Status.VETOED, Trade.Status.COMPLETED]   # everyone in the league can see these
REVIEW_DAYS = 2


def process_due_trades(now: datetime | None = None) -> int:
    """Process every ACCEPTED trade whose review has ended. Returns how many finished (completed or failed)."""
    now = now or timezone.now()
    due = Trade.objects.filter(status=Trade.Status.ACCEPTED, review_ends_at__lte=now)
    return sum(1 for trade in due if process_trade(trade))


@transaction.atomic
def process_trade(trade: Trade) -> bool:
    """Move the players of an accepted trade whose review is over. Returns False when it must wait (a player is locked)
    or isn't ACCEPTED anymore; True when it finished (COMPLETED or FAILED)."""
    trade = Trade.objects.select_for_update().select_related("league__season", "proposer", "receiver").get(pk=trade.pk)
    if trade.status != Trade.Status.ACCEPTED:
        return False                         # vetoed, or failed by another trade since the list was made

    # 1. Find every row again, and check locks and fits. Any 400 here means the trade fails.
    try:
        week, proposer_rows = current_entries(trade.proposer)
        _, receiver_rows = current_entries(trade.receiver)
        items = list(trade.items.select_related("team", "player", "nfl_team"))

        def side(team: FantasyTeam, kind: str, rows: dict[int, RosterEntry]) -> list[RosterEntry]:
            """This team's items of this kind, as this week's rows."""
            return rows_for([i for i in items if i.team_id == team.id and i.kind == kind], rows)

        p_send, p_drop = side(trade.proposer, TradeItem.Kind.SEND, proposer_rows), side(trade.proposer, TradeItem.Kind.DROP, proposer_rows)
        r_send, r_drop = side(trade.receiver, TradeItem.Kind.SEND, receiver_rows), side(trade.receiver, TradeItem.Kind.DROP, receiver_rows)

        started = started_team_ids(trade.league.season, week)
        if any(entry_nfl_team_id(e) in started for e in p_send + p_drop + r_send + r_drop):
            return False                     # wait: nothing changes, try again next pass

        check_fits(trade.proposer, proposer_rows, p_send + p_drop, r_send)
        check_fits(trade.receiver, receiver_rows, r_send + r_drop, p_send)
    except ValidationError as error:
        finish(trade, Trade.Status.FAILED, first_error(error))
        return True

    # 2. Drops first (each goes on waivers), then the sent rows switch teams and go to the bench
    for e in p_drop + r_drop:
        e.delete()
        start_hold(trade.league, e.player, e.nfl_team)
    for e in p_send:
        e.team, e.slot = trade.receiver, RosterEntry.Slot.BENCH
    for e in r_send:
        e.team, e.slot = trade.proposer, RosterEntry.Slot.BENCH
    RosterEntry.objects.bulk_update(p_send + r_send, ["team", "slot"])

    # 3. Done, and anything else that counted on these players can't happen now
    finish(trade, Trade.Status.COMPLETED)
    fail_conflicts(trade, {(e.player_id, e.nfl_team_id) for e in p_send + p_drop + r_send + r_drop})
    return True


@transaction.atomic
def propose_trade(proposer: FantasyTeam, receiver: FantasyTeam, send_ids: list[int], receive_ids: list[int],
                  drop_ids: list[int]) -> Trade:
    """Offer `send_ids` (proposer's rows) for `receive_ids` (receiver's rows), dropping `drop_ids` to make room. Bots decline at once."""
    league = proposer.league
    trading_acceptable(league)
    if receiver.league_id != league.id or receiver.id == proposer.id:
        raise ValidationError({"receiver_id": "Pick another team in your league."})

    # 1. Turn the ids into this week's rows (each list from the right team)
    week, mine = current_entries(proposer)
    _, theirs = current_entries(receiver)
    send = select_players(mine, send_ids, "send_entry_ids", proposer.name)
    receive = select_players(theirs, receive_ids, "receive_entry_ids", receiver.name)
    drops = select_players(mine, drop_ids, "drop_entry_ids", proposer.name)
    if set(drop_ids) & set(send_ids):
        raise ValidationError({"drop_entry_ids": "You can't drop a player you're trading."})

    # 2. Only the proposer's side can be checked now (the receiver may add drops when accepting)
    check_fits(proposer, mine, send + drops, receive)

    # 3. Save the trade and every item in one bulk_create
    trade = Trade.objects.create(league=league, proposer=proposer, receiver=receiver)
    TradeItem.objects.bulk_create(
        items_for(trade, proposer, send, TradeItem.Kind.SEND)
        + items_for(trade, receiver, receive, TradeItem.Kind.SEND)
        + items_for(trade, proposer, drops, TradeItem.Kind.DROP)
    )

    # 4. Bots never trade
    if receiver.owner_id is None:
        trade.status, trade.note, trade.responded_at = Trade.Status.DECLINED, "Bots don't trade.", timezone.now()
        trade.save(update_fields=["status", "note", "responded_at"])
    return trade


@transaction.atomic
def accept_trade(trade: Trade, drop_ids: list[int]) -> Trade:
    """The receiver accepts, optionally adding drops. Both rosters are re-checked, and the 2-day review starts."""
    trade = Trade.objects.select_for_update().get(pk=trade.pk)      # two clicks at once can't both accept
    if trade.status != Trade.Status.PROPOSED:
        raise ValidationError({"detail": f"This trade is {trade.get_status_display().lower()}, not waiting for an answer."})
    trading_acceptable(trade.league)

    # 1. Everyone in the trade must still be where the offer says
    _, mine = current_entries(trade.receiver)
    _, theirs = current_entries(trade.proposer)
    items = list(trade.items.select_related("team", "player", "nfl_team"))
    give = rows_for([i for i in items if i.team_id == trade.receiver_id and i.kind == TradeItem.Kind.SEND], mine)
    get = rows_for([i for i in items if i.team_id == trade.proposer_id and i.kind == TradeItem.Kind.SEND], theirs)
    their_drops = rows_for([i for i in items if i.team_id == trade.proposer_id and i.kind == TradeItem.Kind.DROP], theirs)
    drops = select_players(mine, drop_ids, "drop_entry_ids", trade.receiver.name)
    if set(d.id for d in drops) & set(g.id for g in give):
        raise ValidationError({"drop_entry_ids": "You can't drop a player you're trading."})

    # 2. Both sides must fit
    check_fits(trade.receiver, mine, give + drops, get)
    check_fits(trade.proposer, theirs, get + their_drops, give)

    # 3. Save the receiver's drops, then start the review
    TradeItem.objects.bulk_create(items_for(trade, trade.receiver, drops, TradeItem.Kind.DROP))
    trade.status, trade.responded_at = Trade.Status.ACCEPTED, timezone.now()
    trade.review_ends_at = trade.responded_at + timedelta(days=REVIEW_DAYS)
    trade.save(update_fields=["status", "responded_at", "review_ends_at"])
    return trade


def close_trade(trade: Trade, status: str) -> None:
    """Decline (receiver) or cancel (proposer) a trade that's still PROPOSED. 400 otherwise."""
    updated = Trade.objects.filter(id=trade.id, status=Trade.Status.PROPOSED).update(status=status, responded_at=timezone.now())
    if updated == 0:
        raise ValidationError({"detail": "This trade isn't waiting for an answer."})


def veto_trade(trade: Trade, note: str = "") -> None:
    """The commissioner cancels a trade during its review. 400 unless it's ACCEPTED."""
    updated = Trade.objects.filter(id=trade.id, status=Trade.Status.ACCEPTED).update(status=Trade.Status.VETOED, note=note or "Vetoed by the commissioner.",
                                                                                     processed_at=timezone.now())
    if updated == 0:
        raise ValidationError({"detail": "Only a trade in review can be vetoed."})


def finish(trade: Trade, status: str, note: str = "") -> None:
    """Close a trade with this status and note, stamped with processed_at."""
    trade.status, trade.note, trade.processed_at = status, note, timezone.now()
    trade.save(update_fields=["status", "note", "processed_at"])


def fail_conflicts(trade: Trade, moved: set[tuple]) -> None:
    """FAIL other open trades in this league that include a player in `moved` ({(player_id, nfl_team_id)}),
    and pending waiver claims that would drop one of them."""
    player_ids = {p for p, t in moved if p}
    team_ids = {t for p, t in moved if t}
    touching = Q(items__player_id__in=player_ids) | Q(items__nfl_team_id__in=team_ids)
    others = (Trade.objects.filter(league=trade.league, status__in=[Trade.Status.PROPOSED, Trade.Status.ACCEPTED])
              .exclude(pk=trade.pk).filter(touching).distinct())
    Trade.objects.filter(id__in=others.values("pk")).update(
        status=Trade.Status.FAILED, note="A player in this trade was traded or dropped.", processed_at=timezone.now())

    other_claims = (WaiverClaim.objects.filter(team__league_id=trade.league, status=WaiverClaim.Status.PENDING)
                    .filter(Q(drop_player_id__in=player_ids) | Q(drop_nfl_team_id__in=team_ids)))
    other_claims.update(status=WaiverClaim.Status.FAILED, note="The player you chose to drop was traded or dropped.",
                                              processed_at=timezone.now())


def check_fits(team: FantasyTeam, entries: dict[int, RosterEntry], leaving: list[RosterEntry],
               arriving: list[RosterEntry]) -> None:
    """400 if, after `leaving` go and `arriving` come, this team would break a position limit or have too many players."""
    keeping = [e for e in entries.values() if e not in leaving]
    counts = Counter(entry_position(e) for e in keeping + arriving)
    for position, count in counts.items():
        if count > POSITION_LIMITS[position]:
            raise ValidationError({"detail": f"{team.name} would have {count} {position}s (the limit is {POSITION_LIMITS[position]}). Add a drop."})
    active = sum(1 for e in keeping if e.slot != RosterEntry.Slot.IR) + len(arriving)
    if active > team.league.roster_size:
        raise ValidationError({"detail": f"{team.name} would have {active} players (the limit is {team.league.roster_size}). Add a drop."})


def trading_acceptable(league: League) -> None:
    """400 unless the league is in season and it isn't past the trade deadline."""
    if league.status != League.Status.IN_SEASON:
        raise ValidationError({"detail": "Trades open after the draft."})
    current_week = league.season.current_week
    if current_week > league.trade_deadline:
        raise ValidationError({"detail": f"The trade deadline (week {league.trade_deadline}) has passed."})


def select_players(entries: dict[int, RosterEntry], ids: list[int], field: str, team_name: str) -> list[RosterEntry]:
    """The roster rows for `ids`, from one team's {id: row} for this week. 400 on `field` for any id that isn't there."""
    rows = []
    for entry_id in ids:
        row = entries.get(entry_id)
        if row is None:
            raise ValidationError({field: f"{entry_id} isn't on {team_name}'s roster this week."})
        rows.append(row)
    return rows


def rows_for(items: list[TradeItem], entries: dict[int, RosterEntry]) -> list[RosterEntry]:
    """This week's row for each item, matched by player/D/ST. 400 if one isn't on that team anymore."""
    by_who = {(e.player_id, e.nfl_team_id): e for e in entries.values()}
    rows = []
    for item in items:
        row = by_who.get((item.player_id, item.nfl_team_id))
        if row is None:
            raise ValidationError({"detail": f"{item.player or item.nfl_team} is no longer on {item.team.name}."})
        rows.append(row)
    return rows


def items_for(trade: Trade, team: FantasyTeam, rows: list[RosterEntry], kind: str) -> list[TradeItem]:
    """Unsaved TradeItems for these rows (who the player is, not the row), ready for bulk_create."""
    return [TradeItem(trade=trade, team=team, player=r.player, nfl_team=r.nfl_team, kind=kind) for r in rows]
