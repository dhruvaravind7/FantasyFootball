from django.core.management.base import BaseCommand
from django.db import transaction

from league import history


class Command(BaseCommand):
    help = "Import a past season's games and stats from nflverse (for draft rankings and training)"

    def add_arguments(self, parser):
        parser.add_argument("--season", type=int, required=True, help="e.g. 2025")

    def handle(self, *args, **options):
        season = options["season"]
        with transaction.atomic():      # all or nothing: a crash halfway doesn't leave half a season saved
            games = history.import_games(season)
            linked = history.bridge_gsis_ids()
            written, skipped = history.import_player_stats(season, games)
            team_lines = history.import_team_stats(season, games)

        self.stdout.write(
            f"{season}: {len(games)} games, {linked} players linked to nflverse, "
            f"{written} player stat lines ({skipped} skipped: not in our players), {team_lines} D/ST lines"
        )
