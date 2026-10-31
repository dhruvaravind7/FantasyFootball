from django.core.management.base import BaseCommand, CommandError

from league.espn import ESPNFormatError
from league.models import Game
from plays.sync import sync_game


class Command(BaseCommand):
    help = "Load drives, plays and game events for one game from ESPN"

    def add_arguments(self, parser):
        parser.add_argument("game_id", help="ESPN game id e.g. 401873010")
    
    def handle(self, *args, **options):
        game = Game.objects.select_related("home_team", "away_team").get(espn_id=options["game_id"])
        try:
            counts = sync_game(game)
        except ESPNFormatError as e:
            raise CommandError(str(e))
        
        self.stdout.write(f"{game}: {counts["drives"]} drives, {counts["plays"]} plays, {counts["events"]} events, {counts["skipped"]} skipped, {counts["unchanged"]} unchanged")