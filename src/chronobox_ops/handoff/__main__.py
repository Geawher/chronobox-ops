"""``python -m chronobox_ops.handoff``: the hand-off command line (the harness starts staging this way)."""

import asyncio

from chronobox_ops.handoff.cli import main

asyncio.run(main())
