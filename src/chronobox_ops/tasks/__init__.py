"""All benchmark tasks, by name."""

from __future__ import annotations

from chronobox_ops.scenario import Scenario
from chronobox_ops.tasks import (
    databases,
    discovery,
    hidden_config_hazards,
    hidden_upgrade_hazards,
    live_ops,
    releases,
    reliability,
    security,
    upgrades,
)

MODULES = (
    live_ops,
    discovery,
    upgrades,
    databases,
    releases,
    reliability,
    security,
    hidden_config_hazards,
    hidden_upgrade_hazards,
)


def _collect() -> dict[str, Scenario]:
    tasks: dict[str, Scenario] = {}
    for module in MODULES:
        for scenario in module.tasks():
            if scenario.name in tasks:
                raise ValueError(f"duplicate task name {scenario.name!r}")
            tasks[scenario.name] = scenario
    return tasks


TASKS: dict[str, Scenario] = _collect()
