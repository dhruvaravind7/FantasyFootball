from collections import Counter
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .draft import POSITION_LIMITS
from .free_agency import current_entries
from .lineups import entry_position
from .models import FantasyTeam, League, RosterEntry, Trade, TradeItem

REVIEW_DAYS = 2


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
