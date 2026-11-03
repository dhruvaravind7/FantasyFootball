from rest_framework import viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.filters import SearchFilter

from .models import Game, Team, Player, PlayerGameStats
from .serializers import GameSerializer, TeamSerializer, PlayerSerializer, GameLogSerializer

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

class PlayerViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Player.objects.select_related("team").order_by("last_name", "first_name")
    serializer_class = PlayerSerializer
    filter_backends = [SearchFilter]
    search_fields = ["first_name", "last_name"]
    
    def get_queryset(self):
        queryset = super().get_queryset()
        
        position = self.request.query_params.get("position")
        if position is not None:
            position = position.upper()
            if position not in Player.Position.values:
                raise ValidationError({"position": f"Must be one of {', '.join(Player.Position.values)}"})
            queryset = queryset.filter(position=position)

        team = self.request.query_params.get("team")
        if team is not None:
            team = team.upper()
            if team not in Team.objects.values_list("abbreviation", flat=True):
                raise ValidationError({"team": f"Must be one of {', '.join(Team.objects.values_list("abbreviation", flat=True))}"})
            queryset = queryset.filter(team__abbreviation=team)
        
        return queryset
    
    @action(detail=True)
    def games(self, request, pk=None):
        player = self.get_object()
        game_stats = PlayerGameStats.objects.filter(player=player).select_related("game__home_team", "game__away_team").order_by("game__kickoff")
        
        items = GameLogSerializer(game_stats, many=True).data
        return Response(items)