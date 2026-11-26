from league.models import Player, Team
from rest_framework import serializers
from django.db import transaction
from rest_framework.exceptions import ValidationError, NotFound
from league.serializers import PlayerSerializer, TeamSerializer

from .draft import POSITION_LIMITS
from .models import PLAYOFF_LAST_WEEK, FantasyTeam, League, ScoringRules, RosterEntry, DraftPick, DraftQueueEntry, PlayerRanking, Matchup, WaiverClaim, Trade, TradeItem

class FantasyTeamSerializer(serializers.ModelSerializer):
    owner = serializers.CharField(source="owner.username", allow_null=True, read_only=True)

    class Meta:
        model = FantasyTeam
        fields = ["id", "name", "owner", "playoff_seed", "waiver_priority"]
        read_only_fields = ["playoff_seed", "waiver_priority"]

    def validate_name(self, value):
        taken = FantasyTeam.objects.filter(league=self.instance.league, name__iexact=value).exclude(pk=self.instance.pk)
        if taken.exists():
            raise ValidationError("Another team in this league already has that name.")
        return value

STARTER_SLOT_FIELDS = ["qb_slots", "rb_slots", "wr_slots", "te_slots", "flex_slots", "dst_slots", "k_slots"]
SLOT_FIELDS = STARTER_SLOT_FIELDS + ["bench_slots"]
MAX_ROSTER = sum(POSITION_LIMITS.values())       # 29: no team can roster more than the position limits allow


class LeagueSerializer(serializers.ModelSerializer):
    commissioner = serializers.CharField(source="commissioner.username", read_only=True)
    teams = FantasyTeamSerializer(many=True, read_only=True)          # many=True: a list of teams
    team_name = serializers.CharField(write_only=True, max_length=40)

    class Meta:
        model = League
        fields = ["id", "name", "size", "status", "invite_code", "commissioner",
                  "qb_slots", "rb_slots", "wr_slots", "te_slots", "flex_slots", "dst_slots", "k_slots", "bench_slots",
                  "teams", "team_name", "draft_time", "pick_seconds", "current_pick", "pick_deadline", "playoff_teams", "trade_deadline", "is_demo"]
        read_only_fields = ["status", "invite_code", "current_pick", "pick_deadline", "is_demo"]
        extra_kwargs = {**{field: {"min_value": 0, "max_value": 3} for field in STARTER_SLOT_FIELDS},
                        "bench_slots": {"min_value": 0, "max_value": 10}}

    def validate_size(self, value):
        if value % 2 != 0 or value < 4 or value > 14:
            raise ValidationError("Must be an even number from 4-14")

        if self.instance is not None:
            count = self.instance.teams.count()
            if value < count:
                raise ValidationError(f"This league already has {count} teams.")

        return value

    def validate_playoff_teams(self, value: int) -> int:
        """A playoff bracket has 4 or 6 teams."""
        if value != 4 and value != 6:
            raise ValidationError("Must have either 4 or 6 playoff teams.")
        return value

    def validate(self, data: dict) -> dict:
        """Playoff teams can't outnumber the league, and the trade deadline must be a regular-season week.
        On a PATCH, a field that wasn't sent keeps its saved value."""
        size = data.get("size", self.instance.size if self.instance else 10)
        playoff_teams = data.get("playoff_teams", self.instance.playoff_teams if self.instance else 4)     # saved value, or 4 when creating
        trade_deadline = data.get("trade_deadline", self.instance.trade_deadline if self.instance else 12)
        last_regular_week = PLAYOFF_LAST_WEEK - (3 if playoff_teams == 6 else 2)     # 14 with 6 playoff teams, 15 with 4
        if not 1 <= trade_deadline <= last_regular_week:
            raise ValidationError({"trade_deadline": f"Must be a regular-season week (1-{last_regular_week})."})
        if playoff_teams > size:
            raise ValidationError({"playoff_teams": f"A {size}-team league can't have {playoff_teams} playoff teams."})

        # The whole roster (starters + bench) must have at least one starter and fit the position limits
        slots = {f: data.get(f, getattr(self.instance, f) if self.instance else League._meta.get_field(f).default) for f in SLOT_FIELDS}
        if sum(slots[f] for f in STARTER_SLOT_FIELDS) == 0:
            raise ValidationError({"detail": "A roster needs at least one starting slot."})
        if sum(slots.values()) > MAX_ROSTER:
            raise ValidationError({"detail": f"A roster can have at most {MAX_ROSTER} players (starters + bench)."})
        return data

    @transaction.atomic
    def create(self, validated_data):
        team_name = validated_data.pop("team_name")
        league = League.objects.create(**validated_data)
        FantasyTeam.objects.create(league=league, owner=league.commissioner, name=team_name)
        ScoringRules.objects.create(league=league)
        return league

class JoinSerializer(serializers.Serializer):
    invite_code = serializers.CharField(max_length=8)
    team_name = serializers.CharField(max_length=40)

class UsernameSerializer(serializers.Serializer):
    username = serializers.CharField()

class TeamIdSerializer(serializers.Serializer):
    team_id = serializers.IntegerField()

class ScoringRulesSerializer(serializers.ModelSerializer):
    class Meta:
        model = ScoringRules
        exclude = ["id", "league"]

class PresetSerializer(serializers.Serializer):
    preset = serializers.ChoiceField(choices=ScoringRules.Preset.choices)

class RosterEntrySerializer(serializers.ModelSerializer):
    """One roster spot: a player or a D/ST, and which slot it's in that week."""
    player = PlayerSerializer(read_only=True)        # null on D/ST rows
    dst = serializers.CharField(source="nfl_team.abbreviation", allow_null=True, read_only=True)    # null on player rows

    class Meta:
        model = RosterEntry
        fields = ["id", "week", "slot", "player", "dst"]

class DraftPickSerializer(serializers.ModelSerializer):
    """One pick on the draft board."""
    team = serializers.CharField(source="team.name", read_only=True)          # the fantasy team's name
    player = PlayerSerializer(read_only=True)
    dst = serializers.CharField(source="nfl_team.abbreviation", allow_null=True, read_only=True)

    class Meta:
        model = DraftPick
        fields = ["number", "team", "player", "dst", "auto"]


class QueueEntrySerializer(serializers.ModelSerializer):
    """One player or D/ST on a team's queue."""
    player = PlayerSerializer(read_only=True)
    dst = serializers.CharField(source="nfl_team.abbreviation", allow_null=True, read_only=True)
    class Meta:
        model = DraftQueueEntry
        fields = ["id", "order", "player", "dst"]


class ChoiceSerializer(serializers.Serializer):
    """Input for picking or queueing: exactly one of player_id / nfl_team_id."""
    player_id = serializers.IntegerField(required=False)
    nfl_team_id = serializers.IntegerField(required=False)

    def validate(self, data):
        # "x" in data is True when that field was sent
        if ("player_id" in data) == ("nfl_team_id" in data):
            raise serializers.ValidationError("Send exactly one of player_id or nfl_team_id.")
        return data

class AddSerializer(ChoiceSerializer):
    """Input for an add: exactly one of player_id / nfl_team_id (from ChoiceSerializer), plus an optional drop."""
    drop_entry_id = serializers.IntegerField(required=False)


class DropSerializer(serializers.Serializer):
    """Input for a drop: the roster row to drop."""
    entry_id = serializers.IntegerField(required=True)

class QueueEntryIdSerializer(serializers.Serializer):
    """Input for queue/remove: which queue entry."""
    entry_id = serializers.IntegerField()

class ClaimIdSerializer(serializers.Serializer):
    """Input for claims/cancel: which pending claim."""
    claim_id = serializers.IntegerField()

class RankingSerializer(serializers.ModelSerializer):
    """One player or D/ST in a league's rankings, as shown in the free-agent list."""
    player = PlayerSerializer(read_only=True)
    dst = serializers.CharField(source="nfl_team.abbreviation", allow_null=True, read_only=True)             # same as RosterEntrySerializer.dst

    class Meta:
        model = PlayerRanking
        fields = ["rank", "position", "player", "dst", "points_per_game"]

def resolve_choice(data) -> dict:
    """{"player": Player} or {"nfl_team": Team} from ChoiceSerializer's validated data. 404 if the id doesn't exist."""
    if "player_id" in data:
        player = Player.objects.filter(id=data["player_id"]).first()
        if player is None:
            raise NotFound("No player with that id.")
        return {"player": player}

    if "nfl_team_id" in data:
        team = Team.objects.filter(id=data["nfl_team_id"]).first()
        if team is None:
            raise NotFound("No team with that id")
        return {"nfl_team": team}

class LineupMoveSerializer(serializers.Serializer):
    entry_id = serializers.IntegerField()
    slot = serializers.ChoiceField(choices=RosterEntry.Slot.choices)
    swap_with = serializers.IntegerField(required=False)

class MatchupSerializer(serializers.ModelSerializer):
    """One week's head-to-head game. The view adds the scores (live or final)."""
    home = FantasyTeamSerializer(read_only=True)
    away = FantasyTeamSerializer(read_only=True)

    class Meta:
        model = Matchup
        fields = ["id", "week", "home", "away", "is_final", "playoff_round"]

class StandingSerializer(serializers.Serializer):
    """One row of the standings table (built by matchups.standings)."""
    rank = serializers.IntegerField()
    team = FantasyTeamSerializer()
    wins = serializers.IntegerField()
    losses = serializers.IntegerField()
    ties = serializers.IntegerField()
    win_pct = serializers.DecimalField(max_digits=4, decimal_places=3)      # "0.750"
    points_for = serializers.DecimalField(max_digits=7, decimal_places=2)
    points_against = serializers.DecimalField(max_digits=7, decimal_places=2)

class ClaimSerializer(serializers.ModelSerializer):
    """One waiver claim: who's claimed, who'd be dropped, and how it turned out."""
    player = PlayerSerializer(read_only=True)
    dst = serializers.CharField(source="nfl_team.abbreviation", allow_null=True, read_only=True)
    drop_player = PlayerSerializer(read_only=True)                        # same as player
    drop_dst = serializers.CharField(source="drop_nfl_team.abbreviation", allow_null=True, read_only=True)

    class Meta:
        model = WaiverClaim
        fields = ["id", "order", "player", "dst", "drop_player", "drop_dst", "status", "note", "created_at", "processed_at"]

class TradeItemSerializer(serializers.ModelSerializer):
    """One player in a trade: who gives him up, whether he's sent or dropped, and who he is."""
    team = serializers.CharField(source="team.name", read_only=True)
    player = PlayerSerializer(read_only=True)
    dst = serializers.CharField(source="nfl_team.abbreviation", allow_null=True, read_only=True)

    class Meta:
        model = TradeItem
        fields = ["team", "kind", "player", "dst"]


class TradeSerializer(serializers.ModelSerializer):
    """A trade with both teams' names and every item."""
    proposer = serializers.CharField(source="proposer.name", read_only=True)
    receiver = serializers.CharField(source="receiver.name", read_only=True)
    items = TradeItemSerializer(many=True, read_only=True)

    class Meta:
        model = Trade
        fields = ["id", "proposer", "receiver", "status", "note", "items", "created_at", "responded_at", "review_ends_at", "processed_at"]


class ProposeSerializer(serializers.Serializer):
    """Input for a proposal: the other team, the rows each side sends (at least one each), and optional drops."""
    receiver_id = serializers.IntegerField()
    send_entry_ids = serializers.ListField(child=serializers.IntegerField(), allow_empty=False)
    receive_entry_ids = serializers.ListField(child=serializers.IntegerField(), allow_empty=False)
    drop_entry_ids = serializers.ListField(child=serializers.IntegerField(), required=False, default=list)


class DropsSerializer(serializers.Serializer):
    """Input for an accept: optional drops."""
    drop_entry_ids = serializers.ListField(child=serializers.IntegerField(), required=False, default=list)
