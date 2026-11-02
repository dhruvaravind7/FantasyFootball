from rest_framework import serializers

from .models import Game, Team

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