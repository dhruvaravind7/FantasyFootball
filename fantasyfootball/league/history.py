import csv
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import requests
from django.db import transaction

from league.models import Game, Player, PlayerGameStats, Season, Team, TeamGameStats

NFLVERSE = "https://github.com/nflverse/nflverse-data/releases/download"
EASTERN = ZoneInfo("America/New_York")
NFLVERSE_TEAMS = {"LA": "LAR", "WAS": "WSH"}      # nflverse abbreviation -> yours (all others already match)

PLAYER_FIELDS = {
    "completions": "pass_completions", "attempts": "pass_attempts", "passing_yards": "pass_yards",
    "passing_tds": "pass_tds", "passing_interceptions": "interceptions_thrown",
    "carries": "rush_attempts", "rushing_yards": "rush_yards", "rushing_tds": "rush_tds",
    "receptions": "receptions", "targets": "targets", "receiving_yards": "rec_yards", "receiving_tds": "rec_tds",
    "fumbles_total": "fumbles", "fumbles_lost_total": "fumbles_lost",
    "def_interceptions": "def_interceptions", "def_tds": "def_tds",
    "fg_made": "fg_made", "fg_att": "fg_attempts", "pat_made": "xp_made", "pat_att": "xp_attempts",
}

def fetch_csv(path: str) -> list[dict]:
    """Download one nflverse file, e.g. "schedules/games.csv", and return its rows as dicts."""
    response = requests.get(f"{NFLVERSE}/{path}", timeout=60)
    response.raise_for_status()
    return list(csv.DictReader(response.text.splitlines()))

def import_games(season_year: int) -> dict[str, Game]:
    """Create the season's regular-season games. Returns {nflverse game_id: Game} for the stats imports."""
    season, _ = Season.objects.get_or_create(year=season_year)
    teams = {t.abbreviation: t for t in Team.objects.all()}
    games = {}

    for row in fetch_csv("schedules/games.csv"):
        if int(row["season"]) != season_year or row["game_type"] != "REG":
            continue
        
        away_team = row["away_team"]
        if away_team in NFLVERSE_TEAMS:
            away_team = NFLVERSE_TEAMS[away_team]
        away_team = teams[away_team]
        
        home_team = row["home_team"]
        if home_team in NFLVERSE_TEAMS:
            home_team = NFLVERSE_TEAMS[home_team]
        home_team = teams[home_team]
        
        time = row["gameday"] + " " + row["gametime"]
        time = datetime.strptime(time, "%Y-%m-%d %H:%M").replace(tzinfo=EASTERN)
        
        game, _ = Game.objects.update_or_create(espn_id=row["espn"], defaults={"season": season, "week": int(row["week"]), "home_team": home_team, "away_team": away_team, "kickoff": time,
                                                                     "status": Game.Status.FINAL, "home_score": int(row["home_score"]), "away_score": int(row["away_score"])})
        
        games[row["game_id"]] = game
        
    return games

def bridge_gsis_ids() -> int:
    """Fill Player.gsis_id from nflverse's id file. Returns how many players were linked."""
    players_dict = {}
    changed_players = []
    
    for row in fetch_csv("players/players.csv"):
        if row["espn_id"] != "" and row["gsis_id"] != "":
            players_dict[row["espn_id"]] = row["gsis_id"]
    
    for player in Player.objects.all():
        if player.espn_id in players_dict:
            player.gsis_id = players_dict[player.espn_id]
            changed_players.append(player)
    
    Player.objects.bulk_update(changed_players, ["gsis_id"])
    return len(changed_players)

def import_player_stats(season_year: int, games: dict[str, Game]) -> tuple[int, int]:
    """One PlayerGameStats row per player per game. Returns (written, skipped)."""
    players = {p.gsis_id: p for p in Player.objects.filter(gsis_id__isnull=False)}
    teams = {t.abbreviation: t for t in Team.objects.all()}
    written = skipped = 0

    for row in fetch_csv(f"stats_player/stats_player_week_{season_year}.csv"):
        if row["season_type"] != "REG":
            continue
        if row["player_id"] not in players:
            skipped += 1
            continue
        
        stats_dict = {ours: int(row[theirs]) for theirs, ours in PLAYER_FIELDS.items()}
        stats_dict["two_point_conversions"] = (int(row["passing_2pt_conversions"]) + int(row["rushing_2pt_conversions"])
                                               + int(row["receiving_2pt_conversions"]))
        stats_dict["fg_made_0_39"] = int(row["fg_made_0_19"]) + int(row["fg_made_20_29"]) + int(row["fg_made_30_39"])
        stats_dict["fg_made_40_49"] = int(row["fg_made_40_49"])
        stats_dict["fg_made_50_plus"] = int(row["fg_made_50_59"]) + int(row["fg_made_60_"])
        stats_dict["tackles"] = int(row["def_tackles_solo"]) + int(row["def_tackle_assists"])
        stats_dict["kick_return_tds"] = int(row["special_teams_tds"])    # nflverse doesn't split kick/punt returns; scoring adds both
        stats_dict["sacks"] = Decimal(row["def_sacks"])                   # split sacks, e.g. "0.5"

        team_abbr = NFLVERSE_TEAMS.get(row["team"], row["team"])
        PlayerGameStats.objects.update_or_create(game=games[row["game_id"]], player=players[row["player_id"]],
                                                 defaults={"team": teams[team_abbr], **stats_dict})
        written += 1
        
    return written, skipped

def import_team_stats(season_year: int, games: dict[str, Game]) -> int:
    """One TeamGameStats (D/ST) row per team per game. Returns how many."""
    teams = {t.abbreviation: t for t in Team.objects.all()}
    rows = fetch_csv(f"stats_team/stats_team_week_{season_year}.csv")
    by_game_team = {(row["game_id"], row["team"]): row for row in rows if row["season_type"] == "REG"}

    written = 0
    for (game_id, abbr), row in by_game_team.items():
        game = games[game_id]
        team = teams[NFLVERSE_TEAMS.get(abbr, abbr)]
        opponent_row = by_game_team[(game_id, row["opponent_team"])]

        opponent_score = game.away_score if team == game.home_team else game.home_score
        # Approximate: nflverse has no scoring-play list, so assume every return TD got its extra point.
        # A missed or 2-point try after one of those TDs makes this off by a point or two.
        opponent_non_offense = (7 * (int(opponent_row["def_tds"]) + int(opponent_row["special_teams_tds"]))
                                + 2 * int(opponent_row["def_safeties"]))

        TeamGameStats.objects.update_or_create(
            game=game,
            team=team,
            defaults={
                "points_allowed": max(opponent_score - opponent_non_offense, 0),
                "sacks": int(row["def_sacks"]),
                "interceptions": int(row["def_interceptions"]),
                "fumble_recoveries": int(row["fumble_recovery_opp"]),
                "tds": int(row["def_tds"]) + int(row["special_teams_tds"]),
                "safeties": int(row["def_safeties"]),
                "blocked_kicks": int(row["def_punt_blocks"]) + int(row["def_fg_blocks"]),
            },
        )
        written += 1
    return written
