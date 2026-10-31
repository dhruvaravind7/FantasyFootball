import logging
import time

import requests
from django.core.management.base import BaseCommand

from league.models import Game
from league.sync import sync_scoreboard
from plays.sync import sync_game

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = "Poll ESPN during games and keep scores and plays up to date"
    
    def add_arguments(self, parser):
        parser.add_argument("--interval", type=int, default=15, help="Seconds between polls while games are live")
        parser.add_argument("--idle-interval", type=int, default=300, help="Seconds between polls when nothing is live")
        parser.add_argument("--once", action="store_true", help="Poll one time and exit (for testing)")
        
    def handle(self, *args, **options):
        self.stdout.write("Polling ESPN. Ctrl + C to stop")
        try:
            while True:
                live_count = self.poll()
                if options["once"]:
                    break
                time.sleep(options["interval"] if live_count else options["idle_interval"])
        except KeyboardInterrupt:
            self.stdout.write("Stopped")
            
    def poll(self) -> int:
        """One round. Returns how many games are in progress."""
        
        was_live = set(Game.objects.filter(status=Game.Status.IN_PROGRESS).values_list("id", flat=True))
        try:
            games = sync_scoreboard()
        except requests.RequestException as e:
            logger.warning("Couldn't fetch the scoreboard: %s", e)
            return len(was_live)
        to_sync = [
            g for g in games
            if g.status == Game.Status.IN_PROGRESS
            or (g.status == Game.Status.FINAL and g.id in was_live)
        ]
        for game in to_sync:
            try:
                counts = sync_game(game)
                self.stdout.write(
                    f"{game}  {game.get_status_display()}  "
                    f"{game.away_team.abbreviation} {game.away_score} - {game.home_score} {game.home_team.abbreviation}  "
                    f"+{counts['plays']} plays"
                )
            except requests.RequestException as e:
                logger.warning(f"Couldn't sync {game}: {e}")
        
        return sum(1 for g in games if g.status == Game.Status.IN_PROGRESS)