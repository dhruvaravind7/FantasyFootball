from rest_framework import viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import Game, Team
from .serializers import GameSerializer, TeamSerializer

from plays.models import Play, GameEvent
from plays.serializers import GameEventSerializer, PlaySerializer

class TeamViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Team.objects.all()
    serializer_class = TeamSerializer

class GameViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Game.objects.select_related("home_team", "away_team")
    serializer_class = GameSerializer
    
    def get_queryset(self):
        queryset = super().get_queryset()
        
        week = self.request.query_params.get("week")              # Get the parameter for week (/api/games/?week=8). Gets the 8
        if week is not None:
            if not week.isdigit():
                raise ValidationError({"week": "Must be a number"})
            queryset = queryset.filter(week=week)                 # Filters the results by week=8
            
        season = self.request.query_params.get("season")
        if season is not None:
            if not season.isdigit():
                raise ValidationError({"season": "Must be a number"})
            queryset = queryset.filter(season__year=season)

        return queryset
    
    @action(detail=True)
    def timeline(self, request, pk=None):
        game = self.get_object()
        plays = Play.objects.filter(drive__game=game).select_related("drive__offense")
        events = GameEvent.objects.filter(game=game).select_related("team")
        
        items = PlaySerializer(plays, many=True).data + GameEventSerializer(events, many=True).data
        items.sort(key=lambda x: x["number"])
        
        return Response(items)