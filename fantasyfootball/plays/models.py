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
        EXTRA_POINT = "extra_point", "Extra Point"
        QB_KNEEL = "qb_kneel", "QB Kneel"
        QB_SPIKE = "qb_spike", "QB Spike"
        NO_PLAY = "no_play", "No Play"
    
    drive = models.ForeignKey(Drive, on_delete=models.CASCADE, related_name="plays")
    number = models.PositiveSmallIntegerField()
    
    quarter = models.PositiveSmallIntegerField()
    clock_seconds = models.PositiveSmallIntegerField()
    
    down=models.PositiveSmallIntegerField(blank=True, null=True)
    distance = models.PositiveSmallIntegerField(null=True, blank=True)
    yardline_100 = models.PositiveSmallIntegerField()
    
    play_type = models.CharField(choices=PlayType.choices)
    yards_gained = models.IntegerField(default=0)
    description = models.TextField(blank=True)
    
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
            
        return f"Q{self.quarter} {self.down}{suffix} & {self.distance} at {self.yardline_100} - {self.play_type}"