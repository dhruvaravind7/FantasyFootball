import requests

BASE = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"
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

        