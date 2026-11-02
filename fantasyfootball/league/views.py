from django.shortcuts import render
from rest_framework import viewsets

from .models import Game, Team
from .serializers import GameSerializer, TeamSerializer

class TeamViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Team.objects.all()
    serializer_class = TeamSerializer

class GameViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Game.objects.select_related("home_team", "away_team")
    serializer_class = GameSerializer