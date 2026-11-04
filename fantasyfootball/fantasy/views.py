from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from league.models import Season

from .models import League
from .serializers import JoinSerializer, LeagueSerializer


class LeagueViewSet(viewsets.ModelViewSet):
    serializer_class = LeagueSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "post", "head", "options"]

    def get_queryset(self):
        return (
            League.objects.filter(teams__owner=self.request.user).select_related("commissioner").prefetch_related("teams__owner").order_by("-created_at")                                    # newest first ("-" means descending)
        )

    def perform_create(self, serializer):
        season = Season.objects.order_by("-year").first()      # the newest season
        serializer.save(commissioner=self.request.user, season=season)
    
    @action(detail=False, methods=["post"])
    def join(self, request):
        serializer = JoinSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        code = serializer.validated_data["invite_code"]
        team_name = serializer.validated_data["team_name"]

        league = League.objects.filter(invite_code__iexact=code).first()    # iexact, so "k7q2xm" works too
        if league is None:
            raise NotFound("No league has that invite code.")
        if league.status != League.Status.SETUP:
            raise ValidationError({"detail": "This league has already drafted."})
        if league.teams.filter(owner=request.user).exists():
            raise ValidationError({"detail": "You already have a team in this league."})
        if league.teams.count() >= league.size:
            raise ValidationError({"detail": "This league is full."})
        if league.teams.filter(name__iexact=team_name).exists():
            raise ValidationError({"team_name": "Another team in this league already has that name."})
        
        league.teams.create(name=team_name, owner=request.user)
        return Response(LeagueSerializer(league).data, status=status.HTTP_201_CREATED)