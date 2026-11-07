import time

from django.core.management.base import BaseCommand

from fantasy.draft import poll


class Command(BaseCommand):
    help = "Start scheduled drafts and keep live drafts moving (autopicks, expired clocks)"

    def add_arguments(self, parser):
        parser.add_argument("--interval", type=float, default=2, help="Seconds between passes")
        parser.add_argument("--once", action="store_true", help="Run one pass and exit (for testing)")

    def handle(self, *args, **options):
        self.stdout.write("Running drafts. Ctrl + C to stop")
        try:
            while True:
                picks = poll()
                if picks > 0:
                    self.stdout.write(f"{picks} picks made")
                if options["once"]:
                    return
                else:
                    time.sleep(options["interval"])
        except KeyboardInterrupt:
            self.stdout.write("Stopped")