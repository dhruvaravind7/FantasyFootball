from django.db import models

# Create your models here.
class Season(models.Model):
    year = models.IntegerField(unique=True)
    active = models.BooleanField(default=False)
    
    def __str__(self):
        return str(self.year)

class Team(models.Model):
    
    class Conference(models.TextChoices):
        AFC = "AFC", "AFC"
        NFC = "NFC", "NFC"
    
    class Division(models.TextChoices):
        NORTH = "N", "North"
        SOUTH = "S", "South"
        EAST = "E", "East"
        WEST = "W", "West"
    
    city = models.CharField(max_length=50)
    name = models.CharField(max_length=50)
    abbreviation = models.CharField(max_length=4, unique=True)
    conference = models.CharField(max_length=3, choices=Conference.choices)
    division = models.CharField(max_length=5, choices=Division.choices)
    
    class Meta:
        ordering = ['city']
    
    def __str__(self):
        return f"{self.city} {self.name}"
    
    def all_games(self):
        return Game.objects.filter(models.Q(home_team=self) | models.Q(away_team=self))
    
class Player(models.Model):
    
    class Position(models.TextChoices):
        QB = "QB", "Quarterback"
        WR = "WR", "Wide Reciever"
        RB = "RB", "Running Back"
        FB = "FB", "Fullback"
        TE = "TE", "Tight End"
        OT = "OT", "Offensive Tackle"
        G = "G", "Guard"
        C = "C", "Center"
        
        DE = "DE", "Defensive End"
        DT = "DT", "Defensive Tackle"
        LB = "LB", "Linebacker"
        CB = "CB", "Corner  Back"
        S = "S","Safety"
        
        K = "K", "Kicker"
        P = "P", "Punter"        
        LS = "LS", "Long Snapper"
    
    first_name = models.CharField(max_length=50)
    last_name = models.CharField(max_length=50)
    position = models.CharField(max_length=2, choices=Position.choices)
    jersey_number = models.PositiveSmallIntegerField(null=True, blank=True)
    team = models.ForeignKey(
        Team,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="players"
    )
    gsis_id = models.CharField(max_length=20, unique=True, null=True, blank=True)
    
    def __str__(self):
        return f"{self.first_name} {self.last_name} ({self.position})"

class Game(models.Model):
    class Status(models.TextChoices):
        SCHEDULED = "SCHEDULED", "Scheduled"
        IN_PROGRESS = "IN_PROGRESS", "In Progress"
        FINAL = "FINAL", "Final"
    
    season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name="games")
    week = models.PositiveSmallIntegerField()
    home_team = models.ForeignKey(Team, on_delete=models.PROTECT, related_name="home_games")
    away_team = models.ForeignKey(Team, on_delete=models.PROTECT, related_name="away_games")
    kickoff = models.DateTimeField()
    status = models.CharField(default=Status.SCHEDULED, choices=Status.choices)
    home_score = models.PositiveSmallIntegerField(default=0)
    away_score = models.PositiveSmallIntegerField(default=0)
    
    def __str__(self):
        return f"Week {self.week}: {self.away_team} @ {self.home_team}"
    