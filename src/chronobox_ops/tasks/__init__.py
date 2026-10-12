"""All benchmark tasks, by name."""

from __future__ import annotations

from chronobox_ops.scenario import Scenario
from chronobox_ops.tasks import (
    capacity,
    data_cleanup,
    databases,
    discovery,
    downstream,
    hidden_config_hazards,
    hidden_upgrade_hazards,
    live_ops,
    mixed_traffic,
    one_shot,
    one_way,
    performance,
    releases,
    reliability,
    security,
    shared_infra,
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
    capacity,
    performance,
    data_cleanup,
    one_shot,
    shared_infra,
    mixed_traffic,
    downstream,
    one_way,
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
