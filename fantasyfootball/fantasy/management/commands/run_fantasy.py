import time

from django.core.management.base import BaseCommand

from fantasy.draft import poll
from fantasy.matchups import finalize_due_weeks
from fantasy.waivers import process_due_waivers


class Command(BaseCommand):
    help = "Fantasy background work: run drafts (scheduled starts, autopicks, expired clocks), finalize finished weeks, and process waiver claims"

    def add_arguments(self, parser):
        parser.add_argument("--interval", type=float, default=2, help="Seconds between passes")
        parser.add_argument("--once", action="store_true", help="Run one pass and exit (for testing)")

    def handle(self, *args, **options):
        self.stdout.write("Running drafts, finalizing weeks and processing waivers. Ctrl + C to stop")
        try:
            while True:
                picks = poll()
                if picks > 0:
                    self.stdout.write(f"{picks} picks made")
                finalized = finalize_due_weeks()
                if finalized > 0:
                    self.stdout.write(f"Finalized {finalized} league-weeks")
                waivers_claimed = process_due_waivers()
                if waivers_claimed > 0:
                    self.stdout.write(f"{waivers_claimed} waiver claims won")
                if options["once"]:
                    return
                else:
                    time.sleep(options["interval"])
        except KeyboardInterrupt:
            self.stdout.write("Stopped")
