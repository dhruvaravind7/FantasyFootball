from datetime import datetime

from django.db import transaction

from league import espn
from league.espn import ESPNFormatError
from league.models import Game, Season, Team, Player

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

GROUP_TO_ROSTER_STATUS = {
    "offense": Player.RosterStatus.ACTIVE,
    "defense": Player.RosterStatus.ACTIVE,
    "specialTeam": Player.RosterStatus.ACTIVE,
    "injuredReserveOrOut": Player.RosterStatus.RESERVE,
    "practiceSquad": Player.RosterStatus.PRACTICE_SQUAD,
    "suspended": Player.RosterStatus.SUSPENDED
}

INJURY_MAP = {
    "Questionable": Player.InjuryStatus.QUESTIONABLE,
    "Injured Reserve": Player.InjuryStatus.INJURED_RESERVE,
    "Doubtful": Player.InjuryStatus.DOUBTFUL,
    "Out": Player.InjuryStatus.OUT,
    "Suspension": ""
}

@transaction.atomic
def sync_scoreboard(season_year: int | None = None, week: int | None = None) -> list[Game]:
    data = espn.fetch_scoreboard(season=season_year, week=week)
    season, _ = Season.objects.get_or_create(year=data["season"]["year"])
    week_number = data["week"]["number"]
    games = []
    
    for event in data["events"]:
        game_id = event["id"]
        competition = event["competitions"][0]
        kickoff = datetime.fromisoformat(event["date"])
        status = STATUS_MAP[event["status"]["type"]["state"]]
        
        competitors = competition["competitors"]
        home = next(c for c in competitors if c["homeAway"] == "home")
        away = next(c for c in competitors if c["homeAway"] == "away")

        game, _ = Game.objects.update_or_create(
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
        games.append(game)
    return games

@transaction.atomic
def sync_teams():
    teams = espn.fetch_teams()
    if len(teams) != 32:
        raise ESPNFormatError(f"Expected 32 teams from ESPN, got {len(teams)}. Stopping.")

    teams_list = []
    
    for t in teams:
        abbr = t["abbreviation"]
        if abbr not in TEAMS:
            raise ESPNFormatError(f"Unknown team abbreviation {abbr!r}. Add it to TEAMS")
        
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
        teams_list.append(team)
    return teams_list

@transaction.atomic
def sync_players():
    teams = Team.objects.exclude(espn_id__isnull=True)
    if len(teams) != 32:
        raise ESPNFormatError(f"Expected 32 teams in the database, found {len(teams)}. Run sync_teams first.")
    synced_players = []
    seen_ids = set()
    
    for team in teams:
        for athlete in espn.fetch_roster(team.espn_id):
            athlete_id = athlete["id"]
            roster_status = athlete["side"]
            if roster_status not in GROUP_TO_ROSTER_STATUS:
                raise ESPNFormatError(f"{roster_status} is not a valid Roster Status")
            roster_status = GROUP_TO_ROSTER_STATUS[roster_status]
            
            injury_list = athlete["injuries"]
            if len(injury_list) == 0:
                injury_status = ""
            else:
                injury_status = athlete["injuries"][0]["status"]
                if injury_status not in INJURY_MAP:
                    raise ESPNFormatError(f"Unknown injury status: {injury_status}")
                injury_status = INJURY_MAP[injury_status]      
            
            jersey = int(athlete.get("jersey")) if athlete.get("jersey") is not None else None
            position = athlete["position"]["abbreviation"]
            if position not in Player.Position:
                raise ESPNFormatError(f"{position} is not a valid position")
            
            player, created = Player.objects.update_or_create(espn_id=athlete_id, defaults={"roster_status": roster_status, "injury_status": injury_status, 
                                                                                            "jersey_number": jersey, "first_name": athlete["firstName"], "last_name": athlete["lastName"],
                                                                                            "position": position, "team": team})
            seen_ids.add(athlete_id)
            synced_players.append(player)
    Player.objects.exclude(espn_id__in=seen_ids).update(team=None, roster_status="")
    return synced_players