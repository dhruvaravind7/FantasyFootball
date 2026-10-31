from django.core.management.base import BaseCommand, CommandError

from league import sync
from league.espn import ESPNFormatError
from league.models import Game
from plays.sync import sync_game

class Command(BaseCommand):
    help = "Load one week's games and the plays of every game that has started"

    def add_arguments(self, parser):
        parser.add_argument("--season", type=int, help="e.g. 2026 (default: current)")
        parser.add_argument("--week", type=int, help="e.g. 7 (default: current)")

    def handle(self, *args, **options):
        try:
            games = sync.sync_scoreboard(options["season"], options["week"])
            for game in games:
                if game.status == Game.Status.SCHEDULED:     # nothing to load until kickoff
                    continue
                counts = sync_game(game)
                self.stdout.write(f"{game}: {counts['drives']} drives, {counts['plays']} plays, {counts['events']} events, {counts['skipped']} skipped")
        except ESPNFormatError as e:
            raise CommandError(str(e))

        self.stdout.write(f"Synced {len(games)} games")
