from django.contrib import admin
from .models import Season, Team, Player, Game, PlayerGameStats, TeamGameStats


# Register your models here.
admin.site.register(Season)

class PlayerInline(admin.TabularInline):
    model = Player
    extra = 0
    fields = ["first_name", "last_name", "position"]

@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ["abbreviation", "city", "name", "conference", "division"]
    list_filter = ["conference", "division"]
    search_fields = ["city", "name", "abbreviation"]
    inlines = [PlayerInline]
    
@admin.register(Player)
class PlayerAdmin(admin.ModelAdmin):
    list_display = ["first_name", "last_name", "position", "team"]
    list_filter = ["position", "team"]
    search_fields = ["first_name", "last_name"]
    list_select_related = ["team"]
    
@admin.register(Game)
class GameAdmin(admin.ModelAdmin):
    list_display = ['season', "week", 'home_team', 'away_team', 'home_score', 'away_score', 'status']
    list_filter = ["season", "week", "status"]
    list_select_related = ["season", "home_team", "away_team"]
    
@admin.register(PlayerGameStats)
class PlayerGameStatsAdmin(admin.ModelAdmin):
    list_display = ['player', 'game', 'team']
    list_filter = ['game__week', 'team']
    search_fields = ['player__first_name', 'player__last_name']
    list_select_related = ['player', 'team', 'game__home_team', 'game__away_team']
    
@admin.register(TeamGameStats)
class TeamGameStatsAdmin(admin.ModelAdmin):
    list_display = ["game", "team", "points_allowed"]
    list_select_related = ["game__home_team", "game__away_team", "team"]