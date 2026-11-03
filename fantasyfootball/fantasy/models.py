from django.db import models
from django.conf import settings
from django.db.models import Q

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
    
    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["league", "owner"], name="one_team_per_owner_per_league"),
            models.UniqueConstraint(fields=["league", "name"], name="one_team_name_per_league")
        ]

    def __str__(self):
        return f"{self.name} ({self.league.name})"
    