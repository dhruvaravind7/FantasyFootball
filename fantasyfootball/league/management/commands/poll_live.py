import logging
import time

import requests
from django.core.management.base import BaseCommand

from league.espn import ESPNFormatError
from league.models import Game
from league.sync import sync_scoreboard, sync_box_score
from plays.models import SyncError
from plays.sync import sync_game

logger = logging.getLogger(__name__)

# ESPNFormatError covers values we check for. If ESPN renames or removes a field, it shows up as one of the others
FORMAT_ERRORS = (ESPNFormatError, KeyError, TypeError, ValueError, IndexError)

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
        scoreboard_ids = {g.id for g in games}
        # Live last time we looked but not on this week's scoreboard: ESPN moved on while the poller was off
        stuck = Game.objects.filter(id__in=was_live).exclude(id__in=scoreboard_ids).select_related("season")
        for game in stuck:
            try:
                week_games = sync_scoreboard(game.season.year, game.week)
            except requests.RequestException as e:
                logger.warning("Couldn't fetch week %s for %s: %s", game.week, game, e)
                continue
            fresh = next((g for g in week_games if g.id == game.id), None)
            if fresh is None:
                logger.warning("%s is missing from week %s of the scoreboard", game, game.week)
            elif fresh.status != Game.Status.SCHEDULED:
                to_sync.append(fresh)
        unrecorded = set(Game.objects.filter(id__in=scoreboard_ids, status=Game.Status.FINAL, drives__isnull=True).values_list("id", flat=True))    
        queued = {g.id for g in to_sync}
        to_sync += [g for g in games if g.id in unrecorded and g.id not in queued]   
        
        paused = set(SyncError.objects.filter(resolved=False).values_list("game_id", flat=True))
        to_sync = [g for g in to_sync if g.id not in paused]
        
        for game in to_sync:
            try:
                counts = sync_game(game)
                box_counts = sync_box_score(game)
                self.stdout.write(
                    f"{game}  {game.get_status_display()}  "
                    f"{game.away_team.abbreviation} {game.away_score} - {game.home_score} {game.home_team.abbreviation}  "
                    f"+{counts['plays']} plays"
                )
            except requests.RequestException as e:
                logger.warning("Couldn't sync %s: %s", game, e)
            except FORMAT_ERRORS as e:
                SyncError.objects.create(game=game, message=f"{type(e).__name__}: {e}")
                logger.error("Paused %s: %s", game, e)
        
        return sum(1 for g in games if g.status == Game.Status.IN_PROGRESS)

#STEPS TO RESOLVE ERRORS

#1. In the admin, open the error, which says something like “Unknown ESPN play type (Lateral)”.
#2. Add "Lateral" to the map.
#3. Run this: python manage.py sync_game <espn_id>
#4. Restart the poller.
#5. Mark the error resolved.
