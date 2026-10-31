from django.core.management.base import BaseCommand, CommandError

from league import sync
from league.espn import ESPNFormatError

class Command(BaseCommand):
    help = "Load NFL teams and rosters/injuries from ESPN (run daily)"

    def handle(self, *args, **options):
        try:
            teams = sync.sync_teams()
            players = sync.sync_players()
        except ESPNFormatError as e:
            raise CommandError(str(e))

        self.stdout.write(f"Synced {len(teams)} teams")
        self.stdout.write(f"Synced {len(players)} players")
