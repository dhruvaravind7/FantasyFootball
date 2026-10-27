from django.contrib import admin
from .models import Season, Team, Player, Game


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
    list_select_related = ["season", "home_team", "away_team"]