"""Static checks of the task library (behaviour is checked by `chronobox-ops validate`)."""

import re
from typing import get_args

from chronobox_ops.scenario import Difficulty
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
        assert task.difficulty in get_args(Difficulty), task.name
        if task.acked:
            assert task.acked in task.probe, task.name
