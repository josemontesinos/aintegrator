"""``deliver_webhooks``: sign and POST pending events, optionally in a loop."""

from __future__ import annotations

import json
from dataclasses import asdict
from time import sleep
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand

from aintegrator_skylink.webhooks import deliver_pending_events


class Command(BaseCommand):
    """Deliver one batch of webhooks, or keep polling for new events."""

    help = "Deliver pending SkyLink webhooks with fault injection and retries."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--loop", action="store_true", help="keep polling for pending events")
        parser.add_argument(
            "--interval", type=float, default=None, help="seconds between loop iterations"
        )
        parser.add_argument("--batch-size", type=int, default=None, help="maximum events per batch")
        parser.add_argument(
            "--max-batches", type=int, default=None, help="stop after this many batches"
        )

    def handle(self, *args: Any, **options: Any) -> None:
        interval = options["interval"]
        if interval is None:
            interval = settings.SKYLINK_DELIVERY_LOOP_INTERVAL_S
        max_batches = options["max_batches"]
        batch = 0
        while True:
            stats = deliver_pending_events(max_events=options["batch_size"])
            self.stdout.write(json.dumps(asdict(stats), sort_keys=True))
            batch += 1
            if not options["loop"] or (max_batches is not None and batch >= max_batches):
                break
            sleep(interval)
