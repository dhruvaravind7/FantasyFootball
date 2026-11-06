from rest_framework import serializers
from django.db import transaction
from rest_framework.exceptions import ValidationError
from league.serializers import PlayerSerializer

from .models import FantasyTeam, League, ScoringRules, RosterEntry

class FantasyTeamSerializer(serializers.ModelSerializer):
    owner = serializers.CharField(source="owner.username", allow_null=True, read_only=True)

    class Meta:
        model = FantasyTeam
        fields = ["id", "name", "owner"]
    
    def validate_name(self, value):
        taken = FantasyTeam.objects.filter(league=self.instance.league, name__iexact=value).exclude(pk=self.instance.pk)
        if taken.exists():
            raise ValidationError("Another team in this league already has that name.")
        return value

class LeagueSerializer(serializers.ModelSerializer):
    commissioner = serializers.CharField(source="commissioner.username", read_only=True)
    teams = FantasyTeamSerializer(many=True, read_only=True)          # many=True: a list of teams
    team_name = serializers.CharField(write_only=True, max_length=40)

    class Meta:
        model = League
        fields = ["id", "name", "size", "status", "invite_code", "commissioner",
                  "qb_slots", "rb_slots", "wr_slots", "te_slots", "flex_slots", "dst_slots", "k_slots", "bench_slots",
                  "teams", "team_name"]
        read_only_fields = ["status", "invite_code"]

    def validate_size(self, value):
        if value % 2 != 0 or value < 4 or value > 14:
            raise ValidationError("Must be an even number from 4-14")

        if self.instance is not None:
            count = self.instance.teams.count()
            if value < count:
                raise ValidationError(f"This league already has {count} teams.")
        
        return value

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