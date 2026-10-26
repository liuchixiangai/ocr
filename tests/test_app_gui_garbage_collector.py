"""gui_garbage_collector: reference cycles are collected on the GUI thread."""

import gc
import threading

import pytest

import gui_garbage_collector
from gui_garbage_collector import GuiThreadGarbageCollector


@pytest.fixture
def collector(qapp):
    collector = GuiThreadGarbageCollector(interval_ms=60_000)
    yield collector
    collector.stop()
    collector.deleteLater()


def test_it_turns_the_automatic_collection_off_until_stopped(qapp):
    collector = GuiThreadGarbageCollector(interval_ms=60_000)
    try:
        assert not gc.isenabled()
        assert collector.timer.isActive()
    finally:
        collector.stop()
    assert gc.isenabled()
    assert not collector.timer.isActive()


@pytest.mark.parametrize(
    "counts, collected",
    [
        ((10, 0, 0), []),
        ((101, 0, 0), [0]),
        ((101, 11, 0), [0, 1]),
        ((101, 11, 11), [0, 1, 2]),
        ((101, 5, 50), [0]),  # generation 2 only follows generation 1
    ],
)
def test_it_collects_the_generations_over_their_thresholds(
    collector, monkeypatch, counts, collected
):
    calls = []
    monkeypatch.setattr(gui_garbage_collector.gc, "get_count", lambda: counts)
    monkeypatch.setattr(
        gui_garbage_collector.gc,
        "collect",
        lambda generation: calls.append(generation) or 1,
    )
    collector.threshold = (100, 10, 10)
    assert collector.check() == len(collected)
    assert calls == collected


def test_a_cycle_made_on_a_worker_thread_is_finalized_on_the_gui_thread(collector):
    finalized_on = []

    class Node:
        def __del__(self):
            finalized_on.append(threading.current_thread())

    def make_cycle():
        node = Node()
        node.me = node  # unreachable once this returns: only gc can free it
        # allocate as a busy thread would, which used to trigger a collection here
        for _ in range(50_000):
            [object()]

    worker = threading.Thread(target=make_cycle)
    worker.start()
    worker.join()
    assert finalized_on == []

    collector.threshold = (0, 0, 0)
    collector.check()
    assert finalized_on == [threading.main_thread()]
