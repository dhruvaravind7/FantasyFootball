from django.db import models
from django.conf import settings
from decimal import Decimal
from django.db.models import Q, F

import secrets

# Create your models here.
class League(models.Model):
    class Status(models.TextChoices):
        SETUP = "SETUP", "Setup"             # joining open, and the commissioner can change settings
        DRAFTING = "DRAFTING", "Drafting"
        IN_SEASON = "IN_SEASON", "In Season"
        COMPLETE = "COMPLETE", "Complete"
    
    name = models.CharField(max_length=60)
    season = models.ForeignKey("league.Season", on_delete=models.CASCADE, related_name="fantasy_leagues")
    commissioner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="commissioned_leagues")
    size = models.PositiveSmallIntegerField(default=10)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SETUP)
    invite_code = models.CharField(max_length=8, unique=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    qb_slots = models.PositiveSmallIntegerField(default=1)
    wr_slots = models.PositiveSmallIntegerField(default=2)
    rb_slots = models.PositiveSmallIntegerField(default=2)
    te_slots = models.PositiveSmallIntegerField(default=1)
    flex_slots = models.PositiveSmallIntegerField(default=1)
    dst_slots = models.PositiveSmallIntegerField(default=1)
    k_slots = models.PositiveSmallIntegerField(default=1)
    bench_slots = models.PositiveSmallIntegerField(default=7)
    ir_slots = models.PositiveSmallIntegerField(default=1)
    
    draft_time = models.DateTimeField(null=True, blank=True)
    pick_seconds = models.PositiveSmallIntegerField(default=90)
    current_pick = models.PositiveSmallIntegerField(null=True, blank=True)
    pick_deadline = models.DateTimeField(null=True, blank=True)
    paused_seconds_left = models.PositiveSmallIntegerField(blank=True, null=True)
    
    @property
    def roster_size(self) -> int:
        """Spots a team fills in the draft: every starter spot plus bench. IR doesn't count."""
        return self.qb_slots + self.wr_slots + self.rb_slots + self.te_slots + self.flex_slots + self.dst_slots + self.k_slots + self.bench_slots
    
    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(size__in=[4, 6, 8, 10, 12, 14]), name="league_size_even_4_to_14"),
        ]
    
    def __str__(self):
        return self.name
    
    INVITE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    
    def save(self, *args, **kwargs):
        if not self.invite_code:
            self.invite_code = self.new_invite_code()
        super().save(*args, **kwargs)
        
    @classmethod
    def new_invite_code(cls):
        while True:
            code = "".join(secrets.choice(cls.INVITE_ALPHABET) for _ in range(6))
            if not League.objects.filter(invite_code=code).exists():
                return code


class FantasyTeam(models.Model):
    league = models.ForeignKey(League, on_delete=models.CASCADE, related_name="teams")
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True, related_name="fantasy_teams")
    name = models.CharField(max_length=40)
    created_at = models.DateTimeField(auto_now_add=True)
    draft_position = models.PositiveSmallIntegerField(null=True, blank=True)
    autodraft = models.BooleanField(default=False)    
    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["league", "owner"], name="one_team_per_owner_per_league"),
            models.UniqueConstraint(fields=["league", "name"], name="one_team_name_per_league")
        ]

    def __str__(self):
        return f"{self.name} ({self.league.name})"

class RosterEntry(models.Model):
    class Slot(models.TextChoices):
        QB = "QB", "Quarterback"
        RB = "RB", "Running Back"
        WR = "WR", "Wide Receiver"
        TE = "TE", "Tight End"
        FLEX = "FLEX", "Flex"
        DST = "DST", "Defense"
        K = "K", "Kicker"
        BENCH = "BENCH", "Bench"
        IR = "IR", "Injured Reserve"
        
    team = models.ForeignKey(FantasyTeam, on_delete=models.CASCADE, related_name="roster")
    league = models.ForeignKey(League, on_delete=models.CASCADE, related_name="+")
    week = models.PositiveSmallIntegerField()
    player = models.ForeignKey("league.Player", on_delete=models.PROTECT, related_name="rosters", null=True, blank=True)       # Player and not D/ST
    nfl_team = models.ForeignKey("league.Team", on_delete=models.PROTECT, null=True, blank=True, related_name="+")                                # D/ST and not Player
    slot = models.CharField(choices=Slot.choices, max_length=5, default=Slot.BENCH)                                 # Which slot the person/team is in 
    
    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(player__isnull=False, nfl_team__isnull=True) | Q(player__isnull=True, nfl_team__isnull=False),
                name="player_or_dst_not_both",
            ),
            models.UniqueConstraint(fields=["league", "week", "player"], name="one_player_per_week_per_league"),
            models.UniqueConstraint(fields=["league", "week", "nfl_team"], name="one_defense_per_week_per_league")
        ]

    def __str__(self):
        if self.nfl_team_id is None:
            return f"{self.player.first_name} {self.player.last_name} ({self.player.position})"
        return f"{self.nfl_team.city} {self.nfl_team.name} D/ST"

def points(default):
    """A scoring value like 0.04 or -2. max 999.99, 2 decimal places."""
    return models.DecimalField(max_digits=5, decimal_places=2, default=Decimal(default))

class PlayerRanking(models.Model):
    """One league's preseason ranking of every draftable player and D/ST. Built when the draft starts."""
    league = models.ForeignKey(League, on_delete=models.CASCADE, related_name="rankings")
    rank = models.PositiveIntegerField()
    position = models.CharField(max_length=3)        # QB, RB, WR, TE, PK or DST
    player = models.ForeignKey("league.Player", on_delete=models.CASCADE, related_name="ranking", null=True, blank=True)
    nfl_team = models.ForeignKey("league.Team", on_delete=models.CASCADE, related_name="+", null=True, blank=True)
    points_per_game = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)    # None = no history
    value = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)              # over replacement

    class Meta:
        ordering = ["league", "rank"]
        constraints = [
            models.CheckConstraint(condition=Q(player__isnull=False, nfl_team__isnull=True) | Q(player__isnull=True, nfl_team__isnull=False),
                name="ranking_player_or_dst_not_both",
            ),
            models.UniqueConstraint(fields=["league", "player"], name="one_player_per_league"),
            models.UniqueConstraint(fields=["league", "nfl_team"], name="one_defense_per_league"),
            models.UniqueConstraint(fields=["league", "rank"], name="one_rank_per_league")
        ]

class DraftPick(models.Model):
    """One pick in a league's draft."""
    league = models.ForeignKey(League, on_delete=models.CASCADE, related_name="picks")
    number = models.PositiveSmallIntegerField()          # overall pick: 1, 2, 3...
    team = models.ForeignKey(FantasyTeam, on_delete=models.CASCADE, related_name="picks")
    player = models.ForeignKey("league.Player", on_delete=models.PROTECT, related_name="draft_picks", null=True, blank=True)
    nfl_team = models.ForeignKey("league.Team", on_delete=models.PROTECT, related_name="+", null=True, blank=True)
    auto = models.BooleanField(default=False)            # True when autopick made it
    made_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["league", "number"]
        constraints = [
            models.CheckConstraint(condition=Q(player__isnull=False, nfl_team__isnull=True) | Q(player__isnull=True, nfl_team__isnull=False),
                name="draft_pick_player_or_dst",
            ),
            models.UniqueConstraint(fields=["league", "number"], name="one_pick_per_league"),
            models.UniqueConstraint(fields=["league", "player"], name="player_drafted_once_per_league"),
            models.UniqueConstraint(fields=["league", "nfl_team"], name="defense_drafted_once_per_league")
        ]
        
class DraftQueueEntry(models.Model):
    """One player or D/ST on a team's draft queue. Lower `order` = picked first."""
    team = models.ForeignKey(FantasyTeam, on_delete=models.CASCADE, related_name="queue")
    order = models.PositiveSmallIntegerField()
    player = models.ForeignKey("league.Player", on_delete=models.CASCADE, related_name="+", null=True, blank=True)
    nfl_team = models.ForeignKey("league.Team", on_delete=models.CASCADE, related_name="+", null=True, blank=True)

    class Meta:
        ordering = ["team", "order"]
        constraints = [
            models.CheckConstraint(condition=Q(player__isnull=False, nfl_team__isnull=True) | Q(player__isnull=True, nfl_team__isnull=False),
                name="queue_player_or_dst",
            ),
            models.UniqueConstraint(fields=["team", "player"], name="one_player_per_queue"),
            models.UniqueConstraint(fields=["team", "nfl_team"], name="one_defense_per_queue")
        ]

class Matchup(models.Model):
    """One head-to-head game between two fantasy teams in one week."""
    league = models.ForeignKey(League, on_delete=models.CASCADE, related_name="matchups")
    week = models.PositiveSmallIntegerField()
    home = models.ForeignKey(FantasyTeam, on_delete=models.CASCADE, related_name="home_matchups")
    away = models.ForeignKey(FantasyTeam, on_delete=models.CASCADE, related_name="away_matchups")
    home_score = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)   # saved when the week is final
    away_score = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    is_final = models.BooleanField(default=False)

    class Meta:
        ordering = ["league", "week", "id"]
        constraints = [
            models.UniqueConstraint(fields=["league", "week", "home"], name="one_home_game_per_team_per_week"),
            models.UniqueConstraint(fields=["league", "week", "away"], name="one_away_game_per_team_per_week"),
            models.CheckConstraint(condition=~Q(home=F("away")), name="home_team_differs_from_away_team")
        ]

    def __str__(self):
        """'Week 9: Bot 1 @ Me'"""
        return f"Week {self.week}: {self.away.name} @ {self.home.name}"

class ScoringRules(models.Model):
    """How many fantasy points each stat is worth in one league. The defaults are ESPN's PPR."""

    class Preset(models.TextChoices):
        PPR = "PPR", "PPR"
        HALF_PPR = "HALF_PPR", "Half-PPR"
        STANDARD = "STANDARD", "Standard"

    RECEPTION_POINTS = {
        Preset.PPR: Decimal("1"),
        Preset.HALF_PPR: Decimal("0.5"),
        Preset.STANDARD: Decimal("0")
    }

    league = models.OneToOneField(League, on_delete=models.CASCADE, related_name="scoring")

    # Offense
    pass_yard = points("0.04")
    pass_td = points("4")
    interception = points("-2")
    rush_yard = points("0.1")
    rush_td = points("6")
    reception = points("1")
    rec_yard = points("0.1")
    rec_td = points("6")
    two_point = points("2")
    fumble_lost = points("-2")
    return_td = points("6")

    # Kicking
    fg_0_39 = points("3")
    fg_40_49 = points("4")
    fg_50_plus = points("5")
    fg_missed = points("-1")
    xp_made = points("1")
    xp_missed = points("-1")

    # D/ST
    dst_sack = points("1")
    dst_interception = points("2")
    dst_fumble_recovery = points("2")
    dst_td = points("6")
    dst_safety = points("2")
    dst_blocked_kick = points("2")
    
    pa_0 = points("5")
    pa_1_6 = points("4")
    pa_7_13 = points("3")
    pa_14_17 = points("1")
    pa_18_27 = points("0")
    pa_28_34 = points("-1")
    pa_35_45 = points("-3")
    pa_46_plus = points("-5")
    
    def __str__(self):
        return f"Scoring for {self.league}"