from decimal import Decimal

from django.db import models
from django.db.models import Q

# Create your models here.
class Drive(models.Model):
    class Result(models.TextChoices):
        TOUCHDOWN = "TOUCHDOWN", "Touchdown"
        FIELD_GOAL = "FIELD_GOAL", "Field Goal"
        PUNT = "PUNT", "Punt"
        MISSED_FG = "MISSED_FG", "Missed Field Goal"
        TURNOVER = "TURNOVER", "Turnover"
        DOWNS = "DOWNS", "Turnover on Downs"
        SAFETY = "SAFETY", "Safety"
        END_OF_HALF = "END_OF_HALF", "End of half"
        END_OF_GAME = "END_OF_GAME", "End of game"
        
    game = models.ForeignKey("league.Game", on_delete=models.CASCADE, related_name="drives")
    number = models.PositiveSmallIntegerField()
    offense = models.ForeignKey("league.Team", on_delete=models.PROTECT, related_name="drives")    
    start_quarter = models.PositiveSmallIntegerField()
    start_yardline_100 = models.PositiveSmallIntegerField()
    result = models.CharField(choices=Result.choices, blank=True)
    espn_id = models.CharField(max_length=20, unique=True, null=True, blank=True)
    
    class Meta:
        ordering = ["game", "number"]
        constraints = [
            models.UniqueConstraint(fields=["game", "number"],
                                    name="unique_drive_number_per_game"),
        ]
    
    def __str__(self):
        if self.result == "":
            return f"Drive {self.number}: {self.offense} (In progress)"
        return f"Drive {self.number}: {self.offense} ({self.get_result_display()})"

class Play(models.Model):
    class PlayType(models.TextChoices):
        RUN = "run", "Run"
        PASS = "pass", "Pass"
        PUNT = "punt", "Punt"
        FIELD_GOAL = "field_goal", "Field Goal"
        KICKOFF = "kickoff", "Kickoff"
        QB_KNEEL = "qb_kneel", "QB Kneel"
        QB_SPIKE = "qb_spike", "QB Spike"
        NO_PLAY = "no_play", "No Play"
    
    class PatType(models.TextChoices):
        KICK = "KICK", "Kick"
        TWO_POINT_PASS = "TWO_POINT_PASS", "Two Point Pass"
        TWO_POINT_RUSH = "TWO_POINT_RUSH", "Two Point Rush"
    
    drive = models.ForeignKey(Drive, on_delete=models.CASCADE, related_name="plays")
    number = models.PositiveIntegerField()
    espn_id = models.CharField(max_length=20, unique=True, null=True, blank=True)
    
    quarter = models.PositiveSmallIntegerField()
    clock_seconds = models.PositiveSmallIntegerField()
    
    down=models.PositiveSmallIntegerField(blank=True, null=True)
    distance = models.PositiveSmallIntegerField(null=True, blank=True)
    yardline_100 = models.PositiveSmallIntegerField()
    
    play_type = models.CharField(choices=PlayType.choices)
    espn_type = models.CharField(max_length=50, blank=True)    # ESPN's original type, e.g. "Pass Reception"
    yards_gained = models.IntegerField(default=0)
    description = models.TextField(blank=True)

    # Score AFTER the play
    home_score = models.PositiveSmallIntegerField(default=0)
    away_score = models.PositiveSmallIntegerField(default=0)
    pat_type = models.CharField(max_length=20, choices=PatType.choices, blank=True)
    pat_points = models.PositiveSmallIntegerField(default=0)

    
    is_completion = models.BooleanField(default=False)
    is_touchdown = models.BooleanField(default=False)
    is_interception = models.BooleanField(default=False)
    is_fumble_lost = models.BooleanField(default=False)
    is_sack = models.BooleanField(default=False)
    is_first_down = models.BooleanField(default=False)
    
    class Meta:
        ordering = ["drive__number", "number"]
        constraints = [
            models.UniqueConstraint(fields=["drive", "number"],
                                    name="unique_play_number_per_drive"),
            models.CheckConstraint(
                condition=Q(down__isnull = True) | Q(down__gte=1, down__lte=4),
                name="down_between_1_and_4",
            ),
            models.CheckConstraint(
                condition=Q(yardline_100__gte=1, yardline_100__lte=99),
                name="yard_between_1_and_99"
            ),
            models.CheckConstraint(
                condition=Q(quarter__gte=1, quarter__lte=5),
                name="quarter_between_1_and_5"
            )
        ]
    
    def __str__(self):
        if self.down is None:
            return f"Q{self.quarter} {self.drive.offense}: {self.get_play_type_display()}"
            
        
        suffix = ""
        if self.down == 1:
            suffix = "st"
        elif self.down == 2:
            suffix = "nd"
        elif self.down == 3:
            suffix = "rd"
        elif self.down == 4:
            suffix = "th"
            
        return f"Q{self.quarter} {self.down}{suffix} & {self.distance} at {self.yardline_100} - {self.get_play_type_display()}"
    
class PlayParticipant(models.Model):
    class Role(models.TextChoices):
        PASSER = "PASSER", "Passer"
        RECEIVER = "RECEIVER", "Receiver"
        RUSHER = "RUSHER", "Rusher"
        TACKLER = "TACKLER", "Tackler"
        ASSIST_TACKLER = "ASSIST_TACKLER", "Assist Tackler"
        INTERCEPTOR = "INTERCEPTOR", "Interceptor"
        FUMBLER = "FUMBLER", "Fumbler"
        FUMBLE_RECOVERER = "FUMBLE_RECOVERER", "Fumble Recoverer"
        KICKER = "KICKER", "Kicker"
        PUNTER = "PUNTER", "Punter"
        RETURNER = "RETURNER", "Returner"
        SACKER = "SACKER", "Sacker"
        
    play = models.ForeignKey(Play, on_delete=models.CASCADE, related_name="participants")
    player = models.ForeignKey("league.Player", on_delete=models.PROTECT, related_name="participations")
    role = models.CharField(choices=Role.choices)
    credit = models.DecimalField(max_digits=2, decimal_places=1, default=Decimal("1.0"))
    

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["play", "player", "role"], name="unique_player_role_per_play"),
        ]

    def __str__(self):
        if self.role == self.Role.RECEIVER or self.role == self.Role.PASSER or self.role == self.Role.RUSHER:
            return (f"{self.player} ({self.get_role_display()}) - {self.play} for {self.play.yards_gained} yards")            
        return (f"{self.player} ({self.get_role_display()}) - {self.play}")
    
class GameEvent(models.Model):
    """Items in ESPN's play feed that aren't snaps: timeouts, quarter ends, the coin toss, defensive two-pointers."""
    class EventType(models.TextChoices):
        TIMEOUT = "TIMEOUT", "Timeout"
        OFFICIAL_TIMEOUT = "OFFICIAL_TIMEOUT", "Official Timeout"
        TWO_MINUTE_WARNING = "TWO_MINUTE_WARNING", "Two Minute Warning"
        END_PERIOD = "END_PERIOD", "End Period"
        END_HALF = "END_HALF", "End half"
        END_REGULATION = "END_REGULATION", "End Regulation"
        END_GAME = "END_GAME", "End game"
        COIN_TOSS = "COIN_TOSS", "Coin Toss"
        DEFENSIVE_TWO_POINT = "DEFENSIVE_TWO_POINT", "Defensive Two Point"
        
    game = models.ForeignKey("league.Game", on_delete=models.CASCADE, related_name="events")
    number = models.PositiveIntegerField()
    espn_id = models.CharField(max_length=20, unique=True)
    event_type = models.CharField(max_length=20, choices=EventType.choices)
    quarter = models.PositiveSmallIntegerField()
    clock_seconds = models.PositiveSmallIntegerField()
    team = models.ForeignKey("league.Team", on_delete=models.PROTECT, related_name="+", null=True, blank=True)
    description = models.TextField(blank=True)
    
    class Meta:
        ordering = ["game", "number"]
    
    def __str__(self):
        minutes, seconds = divmod(self.clock_seconds, 60)
        return f"Q{self.quarter} {minutes}:{seconds:02d} {self.get_event_type_display()}"    
        