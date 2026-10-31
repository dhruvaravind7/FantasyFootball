import requests

BASE = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"
CORE = "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl"
TIMEOUT = 10

def get_json(url: str, params: dict | None = None) -> dict:
    response = requests.get(url, params=params, timeout=TIMEOUT)
    response.raise_for_status()
    return response.json()

def fetch_teams() -> list[dict]:
    data = get_json(f"{BASE}/teams")
    return [entry["team"] for entry in data["sports"][0]["leagues"][0]["teams"]]

def fetch_scoreboard(season: int | None=None, week: int | None=None) -> dict:
    """With no arguments, ESPN returns the current week."""
    params = {}
    if season and week:
        params = {"dates": season, "seasontype": 2, "week": week}   # seasontype 2 is regular season
    return get_json(f"{BASE}/scoreboard", params)

def fetch_roster(team_espn_id: str) -> list[dict]:
    data = get_json(f"{BASE}/teams/{team_espn_id}/roster")
    final = []
    for entry in data["athletes"]:
        position = entry["position"]
        for athlete in entry["items"]:
            athlete["side"] = position
            final.append(athlete)
    return final

def fetch_drives(game_espn_id: str) -> list[dict]:
    url = f"{CORE}/events/{game_espn_id}/competitions/{game_espn_id}/drives"
    return get_json(url, {"limit": 100})["items"]

def id_from_ref(ref: dict, kind: str) -> str:
    """{"$ref": ".../teams/17?lang=en"} with kind="teams" → "17" """
    return ref["$ref"].split(f"/{kind}/")[1].split("?")[0]

def fetch_plays(game_espn_id: str) -> list[dict]:
    url = f"{CORE}/events/{game_espn_id}/competitions/{game_espn_id}/plays"
    items = get_json(url, {"limit": 400})["items"]
    return sorted(items, key = lambda x: int(x["sequenceNumber"]))

def fetch_athlete(espn_id: str) -> dict:
    url = f"{CORE}/athletes/{espn_id}"
    return get_json(url)
