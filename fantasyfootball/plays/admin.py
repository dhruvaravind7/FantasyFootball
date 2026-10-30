from django.contrib import admin
from .models import Drive, GameEvent, Play, PlayParticipant


class PlayInline(admin.TabularInline):
    model = Play
    extra = 0                      # don't show blank "add another" rows by default
    fields = ["number", "quarter", "down", "distance", "yardline_100", "play_type", "yards_gained"]

class PlayParticipantInline(admin.TabularInline):
    model = PlayParticipant
    extra = 0
    fields = ["player", "role", "credit"]
    autocomplete_fields = ["player"]

@admin.register(Drive)
class DriveAdmin(admin.ModelAdmin):
    list_display = ["__str__", "game", "offense", "start_quarter", "result", "play_count"]
    list_filter = ["result", "offense", "game__season"]
    search_fields = ["offense__abbreviation", "offense__name"]
    list_select_related = ["game__home_team", "game__away_team", "offense"]
    inlines = [PlayInline]
    
    @admin.display(description="Plays")
    def play_count(self, obj):
        return obj.plays.count()
    
@admin.register(Play)
class PlayAdmin(admin.ModelAdmin):
    list_display = ["drive", "number", "quarter", "down", "distance", "yardline_100", "play_type", "yards_gained", "is_touchdown"]
    list_filter = ["quarter", "play_type", "is_touchdown", "down"]
    search_fields = ["description"]
    list_select_related = ["drive__offense"]
    inlines = [PlayParticipantInline]

@admin.register(GameEvent)
class GameEventAdmin(admin.ModelAdmin):
    list_display = ["__str__", "game", "event_type", "team"]
    list_filter = ["event_type", "game__season"]
    list_select_related = ["game__home_team", "game__away_team", "team"]
