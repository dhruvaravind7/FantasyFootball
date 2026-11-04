from django.contrib import admin

from .models import FantasyTeam, League, ScoringRules


class FantasyTeamInline(admin.TabularInline):     
    model = FantasyTeam
    extra = 0                                      

class ScoringRulesInline(admin.StackedInline):
    model = ScoringRules

@admin.register(League)
class LeagueAdmin(admin.ModelAdmin):
    list_display = ["name", "season", "commissioner", "size", "status", "invite_code"]
    readonly_fields = ["invite_code"]
    inlines = [FantasyTeamInline, ScoringRulesInline]


@admin.register(FantasyTeam)
class FantasyTeamAdmin(admin.ModelAdmin):
    list_display = ["name", "league", "owner"]
    list_select_related = ["league", "owner"]