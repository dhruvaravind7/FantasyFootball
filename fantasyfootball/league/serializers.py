from rest_framework import serializers

from .models import Game, Team, Player, PlayerGameStats

class TeamSerializer(serializers.ModelSerializer):
    class Meta:
        model = Team
        fields = ["id", "city", "name", "abbreviation", "conference", "division"]

class GameSerializer(serializers.ModelSerializer):
    home_team = TeamSerializer()
    away_team = TeamSerializer()
    
    class Meta:
        model = Game
        fields = ["id", "week", "kickoff", "status", "home_team", "away_team", "home_score", "away_score"]
        
class PlayerSerializer(serializers.ModelSerializer):
    team = serializers.CharField(source="team.abbreviation", allow_null=True)
    
    class Meta:
        model = Player
        fields = ["id", "first_name", "last_name", "position", "jersey_number", "team", "roster_status", "injury_status"]
        
class GameLogSerializer(serializers.ModelSerializer):
    week = serializers.IntegerField(source="game.week")
    opponent = serializers.SerializerMethodField()
    home = serializers.SerializerMethodField()
    
    class Meta:
        model = PlayerGameStats
        exclude = ["player", "team", "espn_hash", "id"]
    
    def get_home(self, obj):
        return obj.team_id == obj.game.home_team_id

    def get_opponent(self, obj):
        if self.get_home(obj):
            return obj.game.away_team.abbreviation
        else:
            return obj.game.home_team.abbreviation