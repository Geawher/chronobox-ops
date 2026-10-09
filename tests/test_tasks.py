"""Static checks of the task library (behaviour is checked by `chronobox-ops validate`)."""

import re

from chronobox_ops.scenario import DISCARDED_WRITES
from chronobox_ops.tasks import TASKS

HEREDOC = re.compile(r"<<'PY'\n(.*?)\nPY", re.S)


def test_there_are_thirty_tasks_with_unique_names() -> None:
    assert len(TASKS) == 30


def test_embedded_python_compiles() -> None:
    for task in TASKS.values():
        for command in (*task.setup, task.probe, task.check):
            for code in HEREDOC.findall(command):
                compile(code, task.name, "exec")


def test_every_task_is_complete_and_validatable() -> None:
    for task in TASKS.values():
        assert task.prompt and task.setup and task.probe and task.check, task.name
        assert task.reference and task.naive, f"{task.name} needs reference and naive solutions"
        if task.acked:
            assert task.acked in task.probe, task.name


def test_harm_keys_are_printed_by_the_probe() -> None:
    """A misspelled harm key would silently disable that harm check."""
    for task in TASKS.values():
        for key in (*task.harm, *task.tolerated):
            assert key == DISCARDED_WRITES or key in task.probe, f"{task.name}: {key} never printed by the probe"
        assert set(task.tolerated) <= set(task.harm or task.tolerated), f"{task.name}: tolerated key is not harm"


def test_tasks_with_user_data_have_a_corrupt_command() -> None:
    for task in TASKS.values():
        if task.acked:
            assert task.corrupt, f"{task.name} has client writes but no corrupt command"


def test_grader_waits_are_short_unless_traffic_must_be_measured() -> None:
    for task in TASKS.values():
        assert task.observe <= max(task.settle, 20), task.name
