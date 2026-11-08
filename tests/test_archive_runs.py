"""Проверочные прогоны не засоряют список проектов: созданное внутри уходит в архив, остальное не трогается."""
import pytest

from app import config, state


def test_run_projects_go_to_archive_even_on_failure():
    mine = state.create("Мой проект", 1)
    with pytest.raises(RuntimeError):
        with state.archived_test_run() as created:
            test = state.create("Прогон калибровки", 1)
            raise RuntimeError("сбой посреди прогона")
    ids = [p["id"] for p in state.list_projects()]
    assert mine["id"] in ids and test["id"] not in ids
    assert created == [test["id"]]
    assert list((config.data_dir() / "_archive_test_projects").glob(f"*/{test['id']}/state.json"))


def test_create_is_restored_after_run():
    orig = state.create
    with state.archived_test_run():
        assert state.create is not orig
    assert state.create is orig
