from rest_framework import status, viewsets, mixins
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError, PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from django.db import transaction

from league.models import Season
from .models import FantasyTeam, League, ScoringRules, DraftQueueEntry, RosterEntry
from .permissions import IsCommissionerOrReadOnly, IsOwnerOrCommissioner
from .rosters import ensure_week
from .lineups import move
from .serializers import (FantasyTeamSerializer, JoinSerializer, LeagueSerializer, PresetSerializer, RosterEntrySerializer,
                          ScoringRulesSerializer, TeamIdSerializer, UsernameSerializer, DraftPickSerializer, QueueEntrySerializer, ChoiceSerializer,
                          LineupMoveSerializer, AddSerializer, DropSerializer, RankingSerializer, resolve_choice)
from .draft import make_pick, pause_draft, resume_draft, team_on_clock, undo_last_pick
from .scoring import team_week_points
from .free_agency import add_player, drop_player

class LeagueViewSet(viewsets.ModelViewSet):
    serializer_class = LeagueSerializer
    permission_classes = [IsAuthenticated, IsCommissionerOrReadOnly]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        """Only leagues the logged-in user has a team in, newest first."""
        return (
            League.objects.filter(teams__owner=self.request.user).select_related("commissioner").prefetch_related("teams__owner").order_by("-created_at")                                    # newest first ("-" means descending)
        )

    def perform_create(self, serializer):
        """Create the league with the logged-in user as commissioner, in the newest season."""
        season = Season.objects.order_by("-year").first()      # the newest season
        serializer.save(commissioner=self.request.user, season=season)

    def require_setup(self, league, doing):
        """Stop with a 400 unless the league hasn't drafted yet. `doing` fills in the message."""
        if league.status != League.Status.SETUP:
            raise ValidationError({"detail": f"You can't {doing} after the draft."})

    def perform_update(self, serializer):
        """Save settings changes, but only before the draft."""
        self.require_setup(serializer.instance, "change settings")
        serializer.save()

    def perform_destroy(self, instance):
        """Delete the league, but only before the draft."""
        self.require_setup(instance, "delete the league")
        instance.delete()


    @action(detail=False, methods=["post"])
    def join(self, request):
        """POST /leagues/join/: join a league with its invite code and a team name."""
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

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated])
    def leave(self, request, pk=None):
        """POST /leagues/<id>/leave/: leave a league before the draft (the commissioner must hand off first)."""
        league = self.get_object()                       # 404 if you're not in it
        self.require_setup(league, "leave")
        if league.commissioner == request.user:
            raise ValidationError({"detail": "Make someone else commissioner before you leave."})
        league.teams.filter(owner=request.user).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)     # 204 = done, nothing to send back

    @action(detail=True, methods=["post"])
    def transfer(self, request, pk=None):
        """POST /leagues/<id>/transfer/: make another member the commissioner."""
        league = self.get_object()                       # runs the commissioner check (403 for others)
        serializer = UsernameSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Their team in this league. owner__username follows the owner link to its username, like team__abbreviation
        team = league.teams.filter(owner__username=serializer.validated_data["username"]).select_related("owner").first()
        if team is None:
            raise ValidationError({"username": "That user doesn't have a team in this league."})
        league.commissioner = team.owner
        league.save()
        return Response(LeagueSerializer(league).data)

    @action(detail=True, methods=["post"], url_path="remove-team")
    def remove_team(self, request, pk=None):
        """POST /leagues/<id>/remove-team/: the commissioner removes a team before the draft."""
        league = self.get_object()
        self.require_setup(league, "remove teams")
        serializer = TeamIdSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        team = league.teams.filter(id=serializer.validated_data["team_id"]).first()
        if team is None:
            raise ValidationError({"team_id": "No team with that id in this league."})
        if team.owner == league.commissioner:
            raise ValidationError({"team_id": "You can't remove your own team."})

        team.delete()
        return Response(LeagueSerializer(self.get_object()).data)

    @action(detail=True, methods=["get", "patch"])
    def scoring(self, request, pk=None):
        """GET or PATCH /leagues/<id>/scoring/: read the league's scoring rules, or (commissioner, before the draft) change them."""
        league = self.get_object()
        rules, _ = ScoringRules.objects.get_or_create(league=league)
        if request.method == "PATCH":
            self.require_setup(league, "change scoring")
            serializer = ScoringRulesSerializer(rules, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            serializer.save()
        return Response(ScoringRulesSerializer(rules).data)

    @action(detail=True, methods=["post"], url_path="scoring/preset")
    def scoring_preset(self, request, pk=None):
        """POST /leagues/<id>/scoring/preset/: reset the scoring rules to PPR, Half-PPR or Standard."""
        league = self.get_object()
        self.require_setup(league, "change scoring")
        serializer = PresetSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        preset = serializer.validated_data["preset"]
        with transaction.atomic():
            ScoringRules.objects.filter(league=league).delete()
            rules = ScoringRules.objects.create(league=league, reception=ScoringRules.RECEPTION_POINTS[preset])
        return Response(ScoringRulesSerializer(rules).data)

    @action(detail=True, permission_classes=[IsAuthenticated])
    def draft(self, request, pk=None):
        """GET /leagues/<id>/draft/: the draft board (status, pick on the clock, deadline, every pick so far)."""
        league = self.get_object()
        # Only a drafting league has a team on the clock
        on_clock = team_on_clock(league) if league.status == League.Status.DRAFTING else None
        picks = league.picks.select_related("team", "player__team", "nfl_team")
        return Response({
            "status": league.status,
            "current_pick": league.current_pick,
            "pick_deadline": league.pick_deadline,
            "paused_seconds_left": league.paused_seconds_left,
            "on_clock": FantasyTeamSerializer(on_clock).data if on_clock else None,
            "picks": DraftPickSerializer(picks, many=True).data,
        })

    @action(detail=True, methods=["post"], url_path="draft/pick", permission_classes=[IsAuthenticated])
    def draft_pick(self, request, pk=None):
        """POST /leagues/<id>/draft/pick/: the on-the-clock team's owner (or the commissioner) makes its pick."""
        league = self.get_object()
        if league.status != League.Status.DRAFTING:
            raise ValidationError({"detail": "This league isn't drafting"})
        team = team_on_clock(league)
        if request.user != team.owner and request.user != league.commissioner:
            raise PermissionDenied("It's not your pick.")          # 403
        serializer = ChoiceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        pick = make_pick(league, team, **resolve_choice(serializer.validated_data))
        return Response(DraftPickSerializer(pick).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="draft/pause")
    def draft_pause(self, request, pk=None):
        """POST /leagues/<id>/draft/pause/: the commissioner stops the draft clock."""
        league = self.get_object()
        pause_draft(league)
        return self.draft(request, pk)

    @action(detail=True, methods=["post"], url_path="draft/resume")
    def draft_resume(self, request, pk=None):
        """POST /leagues/<id>/draft/resume/: the commissioner restarts the clock with the time it had left."""
        league = self.get_object()
        resume_draft(league)
        return self.draft(request, pk)

    @action(detail=True, methods=["post"], url_path="draft/undo")
    def draft_undo(self, request, pk=None):
        """POST /leagues/<id>/draft/undo/: the commissioner takes back the last pick."""
        league = self.get_object()
        undo_last_pick(league)
        return self.draft(request, pk)

    @action(detail=True, url_path="free-agents", permission_classes=[IsAuthenticated])
    def free_agents(self, request, pk=None):
        """GET /leagues/<id>/free-agents/?position=: unowned players and D/STs this week, best ranked first (top 50)."""
        league = self.get_object()
        week = league.season.current_week
        owned = RosterEntry.objects.filter(league=league, week=week)
        owned_players = owned.exclude(player=None).values_list("player_id", flat=True)
        owned_teams = owned.exclude(nfl_team=None).values_list("nfl_team_id", flat=True)
        available = league.rankings.exclude(player_id__in=owned_players).exclude(nfl_team_id__in=owned_teams)
        position = request.query_params.get("position")
        if position:
            available = available.filter(position=position.upper())
        available = available.select_related("player__team", "nfl_team")[:50]
        return Response(RankingSerializer(available, many=True).data)

class FantasyTeamViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.UpdateModelMixin, viewsets.GenericViewSet):
    serializer_class = FantasyTeamSerializer
    permission_classes = [IsAuthenticated, IsOwnerOrCommissioner]
    http_method_names = ["get", "patch", "post", "head", "options"]

    def get_queryset(self):
        """Teams in the leagues the logged-in user is in, optionally narrowed with ?league=."""
        queryset = FantasyTeam.objects.filter(league__teams__owner=self.request.user).select_related("league__season", "owner").order_by("id")
        league = self.request.query_params.get("league")
        if league is not None:
            if not league.isdigit():
                raise ValidationError({"league": "Must be a number"})
            queryset = queryset.filter(league_id=league)

        return (
            queryset
        )

    def require_owner(self, team):
        """403 unless the logged-in user owns this team or runs its league. Queues are private."""
        if self.request.user != team.owner and self.request.user != team.league.commissioner:
            raise PermissionDenied("Only this team's owner can manage its queue")

    def requested_week(self, request, team: FantasyTeam) -> int | None:
        """?week= as an int, or the season's current week when it's not given. 400 if it's not a number."""
        week = request.query_params.get("week")

        if week is None:
            return team.league.season.current_week
        elif week.isdigit():
            return int(week)
        else:
            raise ValidationError({"week": "Must be a number"})

    @action(detail=True, methods=["get", "post"])
    def queue(self, request, pk=None):
        """GET or POST /teams/<id>/queue/: see the team's draft queue, or add a player/D/ST to the end of it."""
        team = self.get_object()
        self.require_owner(team)
        if request.method == "POST":
            serializer = ChoiceSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            choice = resolve_choice(serializer.validated_data)
            if team.league.picks.filter(**choice).exists():
                raise ValidationError({"detail": "Already Drafted"})
            if team.queue.filter(**choice).exists():
                raise ValidationError("Already in your queue")
            last = team.queue.order_by("-order").values_list("order", flat=True).first() or 0
            DraftQueueEntry.objects.create(team=team, order=last+1, **choice)      # goes to the end of the queue
        return Response(QueueEntrySerializer(team.queue.select_related("player__team", "nfl_team"), many=True).data)

    @action(detail=True, methods=["post"], url_path="queue/remove")
    def queue_remove(self, request, pk=None):
        """POST /teams/<id>/queue/remove/: take one entry off the team's draft queue."""
        team = self.get_object()
        self.require_owner(team)
        deleted, _ = team.queue.filter(id=request.data.get("entry_id")).delete()
        if deleted == 0:
            raise NotFound("That entry isn't in your queue")
        return Response(QueueEntrySerializer(team.queue.select_related("player__team", "nfl_team"), many=True).data)

    @action(detail=True)
    def roster(self, request, pk=None):
        """GET /teams/<id>/roster/?week=: the team's roster for that week (the current week if not given)."""
        team = self.get_object()
        current_week = team.league.season.current_week

        week = self.requested_week(request, team)

        # Only the current week rolls forward: past weeks are history, and future weeks would copy today's roster too early
        if week is not None and week == current_week:
            ensure_week(team, week)

        entries = team.roster.filter(week=week).select_related("player__team", "nfl_team").order_by("id")
        return Response(RosterEntrySerializer(entries, many=True).data)

    @action(detail=True, methods=["post"])
    def autodraft(self, request, pk=None):
        """POST /teams/<id>/autodraft/: turn the team's autodraft on or off."""
        team = self.get_object()          # PATCH-style rule: owner or commissioner (your IsOwnerOrCommissioner)
        on = request.data.get("on")
        if not isinstance(on, bool):      # JSON true/false arrive as Python True/False
            raise ValidationError({"on": "Must be true or false."})
        team.autodraft = on
        team.save(update_fields = ["autodraft"])
        return Response({"autodraft": team.autodraft})


    @action(detail=True, methods=["post"])
    def lineup(self, request, pk=None):
        """POST /teams/<id>/lineup/: move one roster row into a slot for the current week, then return the roster."""
        team = self.get_object()
        serializer = LineupMoveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        move(team, data["entry_id"], data["slot"], data.get("swap_with"))
        return self.roster(request)

    @action(detail=True)
    def points(self, request, pk=None):
        """GET /teams/<id>/points/?week=: every rostered row's fantasy points for that week, plus the team's total (starters only)."""
        team = self.get_object()
        week = self.requested_week(request, team)

        if week is not None and week == team.league.season.current_week:
            ensure_week(team, week)
        result = team_week_points(team, week)
        return Response({
            "week": result["week"],
            "total": str(result["total"]),
            "rows": [
                {**RosterEntrySerializer(row["entry"]).data, "points": str(row["points"]), "has_stats": row["has_stats"]}
                for row in result["rows"]
            ],
        })

    @action(detail=True, methods=["post"])
    def add(self, request, pk=None):
        """POST /teams/<id>/add/: add a free agent (optionally dropping someone in the same move), then return the roster."""
        team = self.get_object()
        serializer = AddSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        add_player(team, **resolve_choice(data), drop_entry_id=data.get("drop_entry_id"))
        return self.roster(request)

    @action(detail=True, methods=["post"])
    def drop(self, request, pk=None):
        """POST /teams/<id>/drop/: drop one player, then return the roster."""
        team = self.get_object()
        serializer = DropSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        drop_player(team, serializer.validated_data["entry_id"])
        return self.roster(request)
