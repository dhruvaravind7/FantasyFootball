from datetime import datetime

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from league import espn
from league.models import Game, Season, Team

TEAMS = {
    "SEA": (Team.Conference.NFC, Team.Division.WEST),
    "SF": (Team.Conference.NFC, Team.Division.WEST),
    "LAR": (Team.Conference.NFC, Team.Division.WEST),
    "ARI": (Team.Conference.NFC, Team.Division.WEST),
    "ATL": (Team.Conference.NFC, Team.Division.SOUTH),
    "TB": (Team.Conference.NFC, Team.Division.SOUTH),
    "NO": (Team.Conference.NFC, Team.Division.SOUTH),
    "CAR": (Team.Conference.NFC, Team.Division.SOUTH),
    "CHI": (Team.Conference.NFC, Team.Division.NORTH),
    "GB": (Team.Conference.NFC, Team.Division.NORTH),
    "DET": (Team.Conference.NFC, Team.Division.NORTH),
    "MIN": (Team.Conference.NFC, Team.Division.NORTH),
    "DAL": (Team.Conference.NFC, Team.Division.EAST),
    "NYG": (Team.Conference.NFC, Team.Division.EAST),
    "PHI": (Team.Conference.NFC, Team.Division.EAST),
    "WSH": (Team.Conference.NFC, Team.Division.EAST),
    "DEN": (Team.Conference.AFC, Team.Division.WEST),
    "LV": (Team.Conference.AFC, Team.Division.WEST),
    "LAC": (Team.Conference.AFC, Team.Division.WEST),
    "KC": (Team.Conference.AFC, Team.Division.WEST),
    "HOU": (Team.Conference.AFC, Team.Division.SOUTH),
    "IND": (Team.Conference.AFC, Team.Division.SOUTH),
    "JAX": (Team.Conference.AFC, Team.Division.SOUTH),
    "TEN": (Team.Conference.AFC, Team.Division.SOUTH),
    "BAL": (Team.Conference.AFC, Team.Division.NORTH),
    "CIN": (Team.Conference.AFC, Team.Division.NORTH),
    "CLE": (Team.Conference.AFC, Team.Division.NORTH),
    "PIT": (Team.Conference.AFC, Team.Division.NORTH),
    "BUF": (Team.Conference.AFC, Team.Division.EAST),
    "MIA": (Team.Conference.AFC, Team.Division.EAST),
    "NE": (Team.Conference.AFC, Team.Division.EAST),
    "NYJ": (Team.Conference.AFC, Team.Division.EAST),
}

STATUS_MAP = {
    "pre": Game.Status.SCHEDULED,
    "in": Game.Status.IN_PROGRESS,
    "post": Game.Status.FINAL
}

class Command(BaseCommand):
    help = "Load NFL teams and one week of games from ESPN"

    def add_arguments(self, parser):
        parser.add_argument("--season", type=int, help="e.g. 2026 (default: current)")
        parser.add_argument("--week", type=int, help="e.g. 7 (default: current)")

    def handle(self, *args, **options):
        self.sync_teams()
        self.sync_games(options["season"], options["week"])
        
    @transaction.atomic
    def sync_teams(self):
        teams = espn.fetch_teams()
        if len(teams) != 32:
            raise CommandError(f"Expected 32 teams from ESPN, got {len(teams)}. Stopping.")

        for t in teams:
            abbr = t["abbreviation"]
            if abbr not in TEAMS:
                raise CommandError(f"Unknown team abbreviation {abbr!r}. Add it to TEAMS")
            
            conference, division = TEAMS[abbr]
            team, created = Team.objects.update_or_create(
                espn_id=t["id"],
                defaults={
                    "abbreviation": abbr,
                    "city": t["location"],
                    "name": t["name"],
                    "conference": conference,
                    "division": division
                },
            )
            self.stdout.write(f"{'Created' if created else 'Updated'} {team}")
    
    @transaction.atomic
    def sync_games(self, season_year, week):
        data = espn.fetch_scoreboard(season=season_year, week=week)
        season, _ = Season.objects.get_or_create(year=data["season"]["year"])
        week_number = data["week"]["number"]
        
        for event in data["events"]:
            game_id = event["id"]
            competition = event["competitions"][0]
            kickoff = datetime.fromisoformat(event["date"])
            status = STATUS_MAP[event["status"]["type"]["state"]]
            
            competitors = competition["competitors"]
            home = next(c for c in competitors if c["homeAway"] == "home")
            away = next(c for c in competitors if c["homeAway"] == "away")

            Game.objects.update_or_create(
                espn_id=game_id,
                defaults={
                    "season": season,
                    "week": week_number,
                    "home_team": Team.objects.get(espn_id=home["id"]),
                    "away_team": Team.objects.get(espn_id=away["id"]),
                    "kickoff": kickoff,
                    "status": status,
                    "home_score": int(home["score"]),
                    "away_score": int(away["score"]),
                },
            )

        self.stdout.write(f"Synced {len(data['events'])} games for {season.year} week {week_number}")
                                
                