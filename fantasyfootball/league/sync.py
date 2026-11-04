from datetime import datetime
from decimal import Decimal

from django.db import transaction

from plays.sync import fingerprint
from league import espn
from league.espn import ESPNFormatError
from league.models import Game, Season, Team, Player, PlayerGameStats, TeamGameStats
from plays.models import Play

# A TD is only "offensive" if it was a run or pass by the team that had the ball
OFFENSE_PLAY_TYPES = {Play.PlayType.RUN, Play.PlayType.PASS}
BLOCKED_KICK_TYPES = {"Blocked Field Goal", "Blocked Punt", "Blocked Punt Touchdown", "Blocked Field Goal Touchdown"}

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

STAT_FIELDS = {
    "passing": {"C/ATT": ("pass_completions", "pass_attempts"), "YDS": "pass_yards", "TD": "pass_tds", "INT": "interceptions_thrown"},
    "rushing": {"CAR": 'rush_attempts', "YDS": "rush_yards", "TD": "rush_tds"},
    "receiving": {"REC": "receptions", "TGTS": "targets", "YDS": "rec_yards", "TD": "rec_tds"},
    "fumbles": {"FUM": "fumbles", "LOST": "fumbles_lost"},
    "defensive": {'TOT': "tackles", 'SACKS': "sacks", "TD": "def_tds"},
    "interceptions": {'INT': "def_interceptions"},
    "kickReturns": {"TD": "kick_return_tds"},
    "puntReturns": {"TD": "punt_return_tds"},
    "kicking": {"FG": ("fg_made", "fg_attempts"), "XP": ("xp_made", "xp_attempts")}
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

@transaction.atomic
def sync_team_stats(game: Game, summary: dict) -> None:
    home, away = game.home_team.espn_id, game.away_team.espn_id
    opponent = {home: away, away: home}                   # opponent[home] is away, and the other way round
    plays = {p.espn_id: p for p in Play.objects.filter(drive__game=game).select_related("drive__offense")}

    # Running totals for each team, filled in below
    lines = {tid: {"tds": 0, "safeties": 0, "blocked_kicks": 0} for tid in (home, away)}
    non_offense_points = {home: 0, away: 0}               # points a team scored WITHOUT its offense
    previous = {home: 0, away: 0}

    # Stages 1 and 2: each scoring play
    for scoring_play in summary["scoringPlays"]:
        scorer = scoring_play["team"]["id"]
        score = {home: scoring_play["homeScore"], away: scoring_play["awayScore"]}
        points = score[scorer] - previous[scorer]
        previous = score
        kind = scoring_play["scoringType"]["abbreviation"]

        if kind == "FG":
            continue                                       # always the offense
        if kind == "TD":
            play = plays.get(scoring_play["id"])
            if play is None:
                continue                                   # live: the plays feed hasn't caught up yet, next poll counts it
            if play.play_type in OFFENSE_PLAY_TYPES and play.drive.offense.espn_id == scorer:
                continue                                   # a normal offensive TD
            else:
                lines[scorer]["tds"] += 1
                non_offense_points[scorer] += points
        elif kind in ("SF", "D2P"):
            non_offense_points[scorer] += points
            if kind == "SF":
                lines[scorer]["safeties"] += 1
        else:
            raise ESPNFormatError(f"Unknown scoring type {kind!r} in {game}. Add it to sync_team_stats.")

    # Stage 3: blocked kicks. The blocking team is the one NOT kicking (not drive.offense)
    for play in plays.values():
        if play.espn_type in BLOCKED_KICK_TYPES:
            blocker = opponent[play.drive.offense.espn_id]
            lines[blocker]["blocked_kicks"] += 1

    # Stage 4: the opponent's box score numbers, and points allowed
    teams = {t["team"]["id"]: {s["name"]: s["displayValue"] for s in t["statistics"]}
             for t in summary["boxscore"]["teams"]}
    final_score = {home: game.home_score, away: game.away_score}
    for tid in (home, away):
        opp = opponent[tid]
        line = lines[tid]
        line["sacks"] = int(teams[opp]["sacksYardsLost"].split("-")[0])     # "2-5" -> "2" -> 2
        line["interceptions"] = int(teams[opp]["interceptions"])
        line["fumble_recoveries"] = int(teams[opp]["fumblesLost"])
        line["points_allowed"] = final_score[opp] - non_offense_points[opp]

        TeamGameStats.objects.update_or_create(
            game=game,
            team = game.home_team if tid == home else game.away_team,
            defaults=line
        )

@transaction.atomic
def sync_box_score(game: Game) -> dict:
    lines = {}
    summary = espn.fetch_summary(game.espn_id)
    for team_block in summary["boxscore"]["players"]:
        team_id = team_block["team"]["id"]
        for category in team_block["statistics"]:
            fields = STAT_FIELDS.get(category["name"])
            if fields is None:
                continue
            for athlete in category["athletes"]:
                stats = dict(zip(category["labels"], athlete["stats"]))
                line = lines.setdefault(athlete["athlete"]["id"], {"team": team_id})
                for label, field in fields.items():
                    value = stats[label]
                    if isinstance(field, tuple):
                        made, attempts = value.split("/")
                        line[field[0]] = int(made)
                        line[field[1]] = int(attempts)
                    elif field == "sacks":
                        line[field] = Decimal(value)
                    else:
                        line[field] = int(value)
    players = {p.espn_id: p for p in Player.objects.filter(espn_id__in=lines)}
    teams = {t.espn_id: t for t in Team.objects.all()}
    existing = dict(
        PlayerGameStats.objects.filter(game=game).values_list("player__espn_id", "espn_hash")
    )
    written = unchanged = 0
    for espn_id, line in lines.items():
        h = fingerprint({k: str(v) for k, v in line.items()})
        if existing.get(espn_id) == h:
            unchanged += 1
            continue

        if espn_id not in players:
            raise ESPNFormatError(f"Box score for {game} has athlete {espn_id}, who isn't in our system")
        stats = {k: v for k, v in line.items() if k != "team"}
        PlayerGameStats.objects.update_or_create(game=game, player=players[espn_id], defaults={"team": teams[line["team"]], "espn_hash": h, **stats})
        written += 1
    sync_team_stats(game, summary)
    return {"written": written, "unchanged": unchanged}
