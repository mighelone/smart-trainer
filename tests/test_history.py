"""History: the bounded chart points and incremental access for viewers."""

from smart_trainer.core.history import History, HistoryPoint


def _point(elapsed: float) -> HistoryPoint:
    return HistoryPoint(elapsed, 200.0, 140, 90.0, None)


def _filled(count: int, maxlen: int = 100) -> History:
    history = History(maxlen=maxlen)
    for elapsed in range(1, count + 1):
        history.add(_point(elapsed))
    return history


def _xs(points: list[HistoryPoint] | None) -> list[float]:
    assert points is not None
    return [p.elapsed for p in points]


def test_add_keeps_the_last_maxlen_points():
    history = _filled(5, maxlen=3)
    assert [p.elapsed for p in history] == [3, 4, 5] and len(history) == 3
    assert history.total == 5  # dropped points still count


def test_since_returns_only_new_points():
    history = _filled(3)
    assert _xs(history.since(history.gen, 0)) == [1, 2, 3]
    history.add(_point(4))
    history.add(_point(5))
    assert _xs(history.since(history.gen, 3)) == [4, 5]
    assert history.since(history.gen, 5) == []


def test_since_asks_for_a_redraw_after_a_restart():
    history = _filled(3)
    gen = history.gen
    history.restart()
    assert len(history) == 0 and history.total == 0 and history.gen == gen + 1
    assert history.since(gen, 3) is None


def test_since_asks_for_a_redraw_when_points_were_dropped():
    history = _filled(5, maxlen=3)
    assert history.since(history.gen, 0) is None  # 5 new, only 3 kept
    assert _xs(history.since(history.gen, 2)) == [3, 4, 5]


def test_since_asks_for_a_redraw_when_the_viewer_is_ahead():
    history = _filled(2)
    assert history.since(history.gen, 5) is None
