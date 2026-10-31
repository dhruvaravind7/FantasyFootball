from django.core.management.base import BaseCommand, CommandError

from league import sync
from league.espn import ESPNFormatError

class Command(BaseCommand):
    help = "Load NFL teams and one week of games from ESPN"

    def add_arguments(self, parser):
        parser.add_argument("--season", type=int, help="e.g. 2026 (default: current)")
        parser.add_argument("--week", type=int, help="e.g. 7 (default: current)")

    def handle(self, *args, **options):
        try:
            teams = sync.sync_teams()
            games = sync.sync_scoreboard(options["season"], options["week"])
            players = sync.sync_players()
        except ESPNFormatError as e:
            raise CommandError(str(e))

        self.stdout.write(f"Synced {len(teams)} teams")
        self.stdout.write(f"Synced {len(games)} games")
        self.stdout.write(f"Synced {len(players)} players")