"""History: the bounded chart points and incremental access for viewers."""

from smart_trainer.core.history import History, HistoryPoint


def _point(x: float) -> HistoryPoint:
    return HistoryPoint(x, 200.0, 140, 90.0, None)


def _filled(n: int, maxlen: int = 100) -> History:
    h = History(maxlen=maxlen)
    for x in range(1, n + 1):
        h.add(_point(x))
    return h


def _xs(points: list[HistoryPoint] | None) -> list[float]:
    assert points is not None
    return [p.elapsed for p in points]


def test_add_keeps_the_last_maxlen_points():
    h = _filled(5, maxlen=3)
    assert [p.elapsed for p in h] == [3, 4, 5] and len(h) == 3
    assert h.total == 5  # dropped points still count


def test_since_returns_only_new_points():
    h = _filled(3)
    assert _xs(h.since(h.gen, 0)) == [1, 2, 3]
    h.add(_point(4))
    h.add(_point(5))
    assert _xs(h.since(h.gen, 3)) == [4, 5]
    assert h.since(h.gen, 5) == []


def test_since_asks_for_a_redraw_after_a_restart():
    h = _filled(3)
    gen = h.gen
    h.restart()
    assert len(h) == 0 and h.total == 0 and h.gen == gen + 1
    assert h.since(gen, 3) is None


def test_since_asks_for_a_redraw_when_points_were_dropped():
    h = _filled(5, maxlen=3)
    assert h.since(h.gen, 0) is None  # 5 new, only 3 kept
    assert _xs(h.since(h.gen, 2)) == [3, 4, 5]


def test_since_asks_for_a_redraw_when_the_viewer_is_ahead():
    h = _filled(2)
    assert h.since(h.gen, 5) is None
