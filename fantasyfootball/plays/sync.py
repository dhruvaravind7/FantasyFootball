import hashlib
import json
from decimal import Decimal

from django.db import transaction

from league import espn
from league.espn import ESPNFormatError
from league.models import Game, Player
from plays.models import Drive, Play, PlayParticipant, GameEvent

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

ROLE_MAP = {
    "passer": PlayParticipant.Role.PASSER,
    "receiver": PlayParticipant.Role.RECEIVER,
    "sackedBy": PlayParticipant.Role.SACKER,
    "rusher": PlayParticipant.Role.RUSHER,
    "tackler": PlayParticipant.Role.TACKLER,
    "assistedBy": PlayParticipant.Role.ASSIST_TACKLER,
    "fumbler": PlayParticipant.Role.FUMBLER,
    "recoverer": PlayParticipant.Role.FUMBLE_RECOVERER,
    "kicker": PlayParticipant.Role.KICKER,
    "punter": PlayParticipant.Role.PUNTER,
    "returner": PlayParticipant.Role.RETURNER,
    "passDefender": PlayParticipant.Role.PASS_DEFENDER,
    "forcedBy": PlayParticipant.Role.FORCED_FUMBLE,
    "scorer": PlayParticipant.Role.SCORER,
    "blocker": PlayParticipant.Role.BLOCKER,
    "patRusher": PlayParticipant.Role.PAT_RUSHER,
    "patPasser": PlayParticipant.Role.PAT_PASSER,
    "patScorer": PlayParticipant.Role.PAT_SCORER
}

IGNORED_ROLES = {"other", "penalized", "snapper", "holder"}
FUMBLE_TYPES = {"Fumble Recovery (Own)", "Fumble Recovery (Opponent)", "Fumble Return Touchdown", "Safety"}
INTERCEPTION_TYPES = {"Pass Interception Return", "Interception Return Touchdown"}

# Only these events belong to one team; for the rest ESPN's "team" is just whoever had the ball.
EVENTS_WITH_TEAM = {GameEvent.EventType.TIMEOUT, GameEvent.EventType.DEFENSIVE_TWO_POINT}

def flags_for(play: dict, play_type: str) -> dict:
    roles = {participant["type"] for participant in play.get("participants", [])}
    espn_type = play["type"]["text"]
    is_interception = espn_type in INTERCEPTION_TYPES
    is_touchdown = play.get("scoringType", {}).get("abbreviation") == "TD"
    
    return {
        "is_interception": is_interception,
        "is_sack": "sackedBy" in roles,
        "is_touchdown": is_touchdown,
        "is_fumble_lost": "fumbler" in roles and not is_interception and play["isTurnover"],
        "is_completion": play_type == Play.PlayType.PASS and "receiver" in roles and espn_type != "Pass Incompletion" and not is_interception,
        "is_first_down": achieved_first_down(play)
    }

def achieved_first_down(play: dict) -> bool:
    if play["type"]["text"] in ("Passing Touchdown", "Rushing Touchdown"):    # the NFL counts offensive TDs as first downs
        return True
    if play["start"]["down"] < 1 or play["start"]["down"] > 4 or play.get("end", {}).get("team") is None:
        return False
    start_team = espn.id_from_ref(play["start"]["team"], "teams")
    end_team = espn.id_from_ref(play["end"]["team"], "teams")
    if start_team != end_team or play["end"]["down"] != 1:
        return False
    
    # 2nd-4th down back to 1st down always means a first down. From 1st down, only if they gained the distance (not a false start)
    return play["start"]["down"] >= 2 or play["statYardage"] >= play["start"]["distance"]

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
        raise ESPNFormatError(f"Can't tell what kind of fumble play {play['id']} was: {sorted(roles)}")

    if espn_type not in PLAY_TYPE_MAP:
        raise ESPNFormatError(f"Unknown ESPN play type {espn_type!r} on play {play['id']}. Add it to PLAY_TYPE_MAP or EVENT_TYPE_MAP.")
    return PLAY_TYPE_MAP[espn_type]


def pat_for(play: dict) -> tuple[str, int]:
    """Return (pat_type, pat_points) for the extra point or two-point try ESPN
    attaches to a play. ("", 0) when there was no try."""
    pat = play.get("pointAfterAttempt")
    if pat is None or pat["text"] == "Not Available":
        return "", 0
    if pat["text"] not in PAT_TYPE_MAP:
        raise ESPNFormatError(f"Unknown point-after type {pat['text']!r} on play {play['id']}. Add it to PAT_TYPE_MAP.")
    return PAT_TYPE_MAP[pat["text"]], pat["value"]

def fingerprint(item: dict) -> str:
    """Same ESPN data gives the same string; any change at all gives a different one."""
    return hashlib.md5(json.dumps(item, sort_keys=True).encode()).hexdigest()

@transaction.atomic
def sync_game(game: Game) -> dict:
    teams_by_espn_id = {game.home_team.espn_id: game.home_team, game.away_team.espn_id: game.away_team}
    plays = espn.fetch_plays(game.espn_id)
    athlete_ids = {espn.id_from_ref(x["athlete"], "athletes") for p in plays for x in p.get("participants", [])}
    players_by_espn_id = {pl.espn_id: pl for pl in Player.objects.filter(espn_id__in=athlete_ids)}
    for athlete_id in athlete_ids:
        if athlete_id not in players_by_espn_id:
            # Played in this game but isn't on any current roster (released since), so load them as a free agent
            athlete = espn.fetch_athlete(athlete_id)
            position = athlete["position"]["abbreviation"]
            if position not in Player.Position.values:
                raise ESPNFormatError(f"Unknown position {position!r} for athlete {athlete_id}")
            jersey = athlete.get("jersey")
            players_by_espn_id[athlete_id] = Player.objects.create(first_name=athlete["firstName"], last_name=athlete["lastName"], position=position,
                                                                    jersey_number=int(jersey) if jersey is not None else None, team=None, espn_id=athlete_id)
    
    # What we saved last time, so we only write what ESPN changed since (one query per table)
    drives_by_espn_id = {drive.espn_id: drive for drive in Drive.objects.filter(game=game)}
    play_hashes = dict(Play.objects.filter(drive__game=game).values_list("espn_id", "espn_hash"))
    event_hashes = dict(GameEvent.objects.filter(game=game).values_list("espn_id", "espn_hash"))
    unchanged = 0
    drives_saved = 0

    # Drives
    for d in espn.fetch_drives(game.espn_id):
        espn_hash = fingerprint(d)
        if d["id"] in drives_by_espn_id and drives_by_espn_id[d["id"]].espn_hash == espn_hash:
            unchanged += 1
            continue
        offense = teams_by_espn_id[espn.id_from_ref(d["team"], "teams")]
        # A drive's yardLine is measured from the home team's goal line
        yardline_100 = d["start"]["yardLine"]
        if offense == game.home_team:
            yardline_100 = 100 - yardline_100
        result = d.get("result") or ""     # a drive still in progress has no result yet
        if result != "":
            if result not in DRIVE_RESULT_MAP:
                raise ESPNFormatError(f"Unknown drive result {result!r} on drive {d['id']}. Add it to DRIVE_RESULT_MAP.")
            result = DRIVE_RESULT_MAP[result]
        
        drive, _ = Drive.objects.update_or_create(espn_id=d["id"], defaults={"game": game, "number": int(d["sequenceNumber"]), "offense": offense, 
                                                                                "start_quarter": d["start"]["period"]["number"], "start_yardline_100": yardline_100,
                                                                                "result": result, "espn_hash": espn_hash})
        drives_by_espn_id[d["id"]] = drive
        drives_saved += 1
    
    # Plays and game events
    plays_saved = 0
    events_saved = 0
    skipped = 0
    for p in plays:
        espn_type = p["type"]["text"]
        espn_hash = fingerprint(p)
        if play_hashes.get(p["id"]) == espn_hash or event_hashes.get(p["id"]) == espn_hash:
            unchanged += 1
            continue
        
        # Game events (timeouts, quarter ends...) aren't snaps, so they get their own table
        if espn_type in EVENT_TYPE_MAP:
            event_type = EVENT_TYPE_MAP[espn_type]
            team = teams_by_espn_id[espn.id_from_ref(p["team"], "teams")] if event_type in EVENTS_WITH_TEAM else None
            GameEvent.objects.update_or_create(espn_id=p["id"], defaults={"game": game, "number": int(p["sequenceNumber"]), "event_type": event_type,
                                                                            "quarter": p["period"]["number"], "clock_seconds": int(p["clock"]["value"]), "team": team,
                                                                            "description": p["text"], "espn_hash": espn_hash})
            events_saved += 1
            continue    
        
        # A real play
        play_type = play_type_for(p)
        pat_type, pat_points = pat_for(p)
        drive = drives_by_espn_id[espn.id_from_ref(p["drive"], "drives")]
        start = p["start"]
        down = None if start["down"] < 1 or start["down"] > 4 else start["down"]
        distance = None if down is None else start["distance"]
        
        yardline_100 = start["yardsToEndzone"]
        if yardline_100 < 1:
            possession = teams_by_espn_id[espn.id_from_ref(start["team"], "teams")]
            yardline_100 = 100 - start["yardLine"] if possession == game.home_team else start["yardLine"]
        if not 1 <= yardline_100 <= 99:
            skipped += 1
            continue
        
        play, _ = Play.objects.update_or_create(espn_id=p["id"], defaults={"number": int(p["sequenceNumber"]), "drive": drive, "quarter": p["period"]["number"], "clock_seconds": int(p["clock"]["value"]),
                                                                    "down": down, "distance": distance, "yardline_100": yardline_100, "play_type": play_type,
                                                                    "espn_type": espn_type, "yards_gained": p["statYardage"], "description": p["text"], "home_score": p["homeScore"],
                                                                    "away_score": p["awayScore"], "pat_type": pat_type, "pat_points": pat_points, "espn_hash": espn_hash, **flags_for(p, play_type)})
        participants = []
        seen = set()
        sacker_count = sum(1 for x in p.get("participants", []) if x["type"] == "sackedBy")
        for x in p.get("participants", []):
            role_type = x["type"]
            if role_type in IGNORED_ROLES:
                continue
            if role_type not in ROLE_MAP:
                raise ESPNFormatError(f"Unknown participant type {role_type!r} on play {p['id']}. Add it to ROLE_MAP or IGNORED_ROLES.")
            
            role_type = ROLE_MAP[role_type]
            if role_type == PlayParticipant.Role.RETURNER and espn_type in INTERCEPTION_TYPES:    # ESPN lists the interceptor as the returner
                role_type = PlayParticipant.Role.INTERCEPTOR
            player = players_by_espn_id[espn.id_from_ref(x["athlete"], "athletes")]
            if (player.id, role_type) in seen:
                continue
            seen.add((player.id, role_type))
            credit = Decimal(1) / sacker_count if role_type == PlayParticipant.Role.SACKER else Decimal("1")
            participants.append(PlayParticipant(play=play, player=player, role=role_type, credit=credit))
        play.participants.all().delete()
        PlayParticipant.objects.bulk_create(participants)
        plays_saved += 1
        
    # drives, plays and events count rows written this time; unchanged ones were already up to date
    return {"drives": drives_saved, "plays": plays_saved, "events": events_saved, "skipped": skipped, "unchanged": unchanged}