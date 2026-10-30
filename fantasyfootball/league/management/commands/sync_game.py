from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from league import espn
from league.models import Game, Team
from plays.models import Drive, Play, GameEvent

DRIVE_RESULT_MAP = {
    "PUNT": Drive.Result.PUNT,
    "TD": Drive.Result.TOUCHDOWN,
    "INT": Drive.Result.TURNOVER,
    "FG": Drive.Result.FIELD_GOAL,
    "MISSED FG": Drive.Result.MISSED_FG,
    "DOWNS": Drive.Result.DOWNS,
    "FUMBLE": Drive.Result.TURNOVER,
    "FUMBLE TD": Drive.Result.TURNOVER,
    "END OF HALF": Drive.Result.END_OF_HALF,
    "END OF GAME": Drive.Result.END_OF_GAME,
    "SAFETY": Drive.Result.SAFETY,
    "SF": Drive.Result.SAFETY,
    "INT TD": Drive.Result.TURNOVER,
    "FUMBLE RETURN TD": Drive.Result.TURNOVER,
    "PUNT TD": Drive.Result.PUNT,
    "BLOCKED PUNT TD": Drive.Result.PUNT,
    "BLOCKED FG": Drive.Result.MISSED_FG,
    "BLOCKED PUNT, DOWNS": Drive.Result.DOWNS,
    "BLOCKED FG, DOWNS": Drive.Result.DOWNS
}

PLAY_TYPE_MAP = {
    "Rush": Play.PlayType.RUN,
    "Pass Reception": Play.PlayType.PASS,
    "Kickoff": Play.PlayType.KICKOFF,
    "Penalty": Play.PlayType.NO_PLAY,
    "Pass Incompletion": Play.PlayType.PASS,
    "Passing Touchdown": Play.PlayType.PASS,
    "Rushing Touchdown": Play.PlayType.RUN,
    "Sack": Play.PlayType.PASS,
    "Pass Interception Return": Play.PlayType.PASS,
    "Sack Opp Fumble Recovery": Play.PlayType.PASS,
    "Punt": Play.PlayType.PUNT,
    "Muffed Punt Recovery (Opponent)": Play.PlayType.PUNT,
    "Field Goal Good": Play.PlayType.FIELD_GOAL,
    "Field Goal Missed": Play.PlayType.FIELD_GOAL, 
    "Blocked Field Goal": Play.PlayType.FIELD_GOAL,
    "Interception Return Touchdown": Play.PlayType.PASS,
    "Punt Return Touchdown": Play.PlayType.PUNT, 
    "Blocked Punt": Play.PlayType.PUNT, 
    "Blocked Punt Touchdown": Play.PlayType.PUNT,
    "Kickoff Return Touchdown": Play.PlayType.KICKOFF,
}

EVENT_TYPE_MAP = {
    "Timeout": GameEvent.EventType.TIMEOUT,
    "Official Timeout": GameEvent.EventType.OFFICIAL_TIMEOUT,
    "Defensive 2pt Conversion": GameEvent.EventType.DEFENSIVE_TWO_POINT,
    "Two-minute warning": GameEvent.EventType.TWO_MINUTE_WARNING,
    "End Period": GameEvent.EventType.END_PERIOD,
    "End of Half": GameEvent.EventType.END_HALF,
    "End of Regulation": GameEvent.EventType.END_REGULATION,
    "End of Game": GameEvent.EventType.END_GAME,
    "Coin Toss": GameEvent.EventType.COIN_TOSS,
}

PAT_TYPE_MAP = {
    "Extra Point Good": Play.PatType.KICK,
    "Two Point Pass": Play.PatType.TWO_POINT_PASS,
    "Extra Point Missed": Play.PatType.KICK,
    "Blocked PAT": Play.PatType.KICK,
    "Two Point Rush": Play.PatType.TWO_POINT_RUSH
}


FUMBLE_TYPES = {"Fumble Recovery (Own)", "Fumble Recovery (Opponent)", "Fumble Return Touchdown", "Safety"}

def play_type_for(play: dict) -> str:
    """Turn ESPN's play type into ours. Fumbles don't say what kind of play they
    happened on, so look at who was involved."""
    espn_type = play["type"]["text"]
    if espn_type in FUMBLE_TYPES:
        roles = {participant["type"] for participant in play.get("participants", [])}
        if "passer" in roles:      # catch-and-fumble, or a sack fumble
            return Play.PlayType.PASS
        if "rusher" in roles:      # run, or a botched snap
            return Play.PlayType.RUN
        if "punter" in roles:      # muffed or fumbled punt return
            return Play.PlayType.PUNT
        raise CommandError(f"Can't tell what kind of fumble play {play['id']} was: {sorted(roles)}")

    if espn_type not in PLAY_TYPE_MAP:
        raise CommandError(f"Unknown ESPN play type {espn_type!r} on play {play['id']}. Add it to PLAY_TYPE_MAP or EVENT_TYPE_MAP.")
    return PLAY_TYPE_MAP[espn_type]


def pat_for(play: dict) -> tuple[str, int]:
    """Return (pat_type, pat_points) for the extra point or two-point try ESPN
    attaches to a play. ("", 0) when there was no try."""
    pat = play.get("pointAfterAttempt")
    if pat is None or pat["text"] == "Not Available":
        return "", 0
    if pat["text"] not in PAT_TYPE_MAP:
        raise CommandError(f"Unknown point-after type {pat['text']!r} on play {play['id']}. Add it to PAT_TYPE_MAP.")
    return PAT_TYPE_MAP[pat["text"]], pat["value"]

# Only these events belong to one team; for the rest ESPN's "team" is just whoever had the ball.
EVENTS_WITH_TEAM = {GameEvent.EventType.TIMEOUT, GameEvent.EventType.DEFENSIVE_TWO_POINT}

class Command(BaseCommand):
    help = "Load drives, plays and game events for one game from ESPN"
    
    def add_arguments(self, parser):
        parser.add_argument("game_id", help="ESPN game id, e.g. 401873010")
        
    @transaction.atomic
    def handle(self, *args, **options):
        game = Game.objects.select_related("home_team", "away_team").get(espn_id=options["game_id"])
        teams_by_espn_id = {game.home_team.espn_id: game.home_team, game.away_team.espn_id: game.away_team}
        
        drives_by_espn_id = {}
        for d in espn.fetch_drives(game.espn_id):
            offense = teams_by_espn_id[espn.id_from_ref(d["team"], "teams")]
            # A drive's yardLine is measured from the home team's goal line
            yardline_100 = d["start"]["yardLine"]
            if offense == game.home_team:
                yardline_100 = 100 - yardline_100
            result = d.get("result") or ""     # a drive still in progress has no result yet
            if result != "":
                if result not in DRIVE_RESULT_MAP:
                    raise CommandError(f"Unknown drive result {result!r} on drive {d['id']}. Add it to DRIVE_RESULT_MAP.")
                result = DRIVE_RESULT_MAP[result]
            
            drive, _ = Drive.objects.update_or_create(espn_id=d["id"],
                                                      defaults={"game": game, "number": int(d["sequenceNumber"]), "offense": offense, "start_quarter": d["start"]["period"]["number"],
                                                                "start_yardline_100": yardline_100, "result": result,})
            drives_by_espn_id[d["id"]] = drive
            
        plays_saved = 0
        events_saved = 0
        skipped = 0
        for p in espn.fetch_plays(game.espn_id):
            espn_type = p["type"]["text"]
            
            if espn_type in EVENT_TYPE_MAP:
                event_type = EVENT_TYPE_MAP[espn_type]
                team = teams_by_espn_id[espn.id_from_ref(p["team"], "teams")] if event_type in EVENTS_WITH_TEAM else None
                GameEvent.objects.update_or_create(espn_id=p["id"], defaults={"game": game, "number": int(p["sequenceNumber"]), "event_type": event_type,
                                                                              "quarter": p["period"]["number"], "clock_seconds": int(p["clock"]["value"]), "team": team,
                                                                              "description": p["text"]})
                events_saved += 1
                continue    
            
            play_type = play_type_for(p)
            pat_type, pat_points = pat_for(p)
            drive = drives_by_espn_id[espn.id_from_ref(p["drive"], "drives")]
            start = p["start"]
            down = None if start["down"] < 1 or start["down"] > 4 else start["down"]
            distance = None if down is None else start["distance"]
            
            yardline_100 = start["yardsToEndzone"]
            if yardline_100 < 1:
                # ESPN sometimes sends 0 by mistake; yardLine is still right, measured from the home goal line
                possession = teams_by_espn_id[espn.id_from_ref(start["team"], "teams")]
                yardline_100 = 100 - start["yardLine"] if possession == game.home_team else start["yardLine"]
            if not 1 <= yardline_100 <= 99:
                skipped += 1
                continue
            
            Play.objects.update_or_create(espn_id=p["id"], defaults={"number": int(p["sequenceNumber"]), "drive": drive, "quarter": p["period"]["number"], "clock_seconds": int(p["clock"]["value"]),
                                                                     "down": down, "distance": distance, "yardline_100": yardline_100, "play_type": play_type,
                                                                     "espn_type": espn_type, "yards_gained": p["statYardage"], "description": p["text"], "home_score": p["homeScore"],
                                                                     "away_score": p["awayScore"], "pat_type": pat_type, "pat_points": pat_points})
            plays_saved += 1
            
        self.stdout.write(f"{game}: {len(drives_by_espn_id)} drives, {plays_saved} plays, {events_saved} events, {skipped} skipped")
