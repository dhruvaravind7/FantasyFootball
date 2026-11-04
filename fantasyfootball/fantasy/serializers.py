from rest_framework import serializers
from .models import League, FantasyTeam
from django.db import transaction
from rest_framework.exceptions import ValidationError


class FantasyTeamSerializer(serializers.ModelSerializer):
    owner = serializers.CharField(source="owner.username", allow_null=True, read_only=True)

    class Meta:
        model = FantasyTeam
        fields = ["id", "name", "owner"]

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
        return value

    @transaction.atomic
    def create(self, validated_data):
        team_name = validated_data.pop("team_name")
        league = League.objects.create(**validated_data)
        FantasyTeam.objects.create(league=league, owner=league.commissioner, name=team_name)
        return league

class JoinSerializer(serializers.Serializer):
    invite_code = serializers.CharField(max_length=8)
    team_name = serializers.CharField(max_length=40)
    
