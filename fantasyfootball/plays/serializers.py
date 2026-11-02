from rest_framework import serializers

from .models import GameEvent, Play

class PlaySerializer(serializers.ModelSerializer):
    kind = serializers.SerializerMethodField()
    drive_number = serializers.IntegerField(source="drive.number")
    offense = serializers.CharField(source="drive.offense.abbreviation")
    
    class Meta:
        model = Play
        fields = ["kind", "id", "number", "drive_number", "offense", "quarter", "clock_seconds",
                  "down", "distance", "yardline_100", "play_type", "yards_gained", "description",
                  "home_score", "away_score", "is_touchdown", "is_first_down"]

    def get_kind(self, obj):
        return "play"

class GameEventSerializer(serializers.ModelSerializer):
    kind = serializers.SerializerMethodField()
    team = serializers.CharField(source="team.abbreviation", allow_null=True)
    
    class Meta:
        model = GameEvent
        fields = ["kind", "id", "number", "event_type", "quarter", "clock_seconds", "team", "description"]
        
    def get_kind(self, obj):
        return "event"