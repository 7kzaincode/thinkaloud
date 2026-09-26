"""UIA descend fallback on a fake element tree (no COM)."""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uia  # noqa: E402


class El:
    def __init__(self, name, rect, children=(), offscreen=False):
        self.name, self.children, self.CurrentIsOffscreen = name, list(children), offscreen
        l, t, r, b = rect
        self.CurrentBoundingRectangle = SimpleNamespace(left=l, top=t, right=r, bottom=b)


class Walker:
    def __init__(self, root):
        self.parent = {}
        stack = [root]
        while stack:
            n = stack.pop()
            for c in n.children:
                self.parent[id(c)] = n
                stack.append(c)

    def GetFirstChildElement(self, el):
        return el.children[0] if el.children else None

    def GetNextSiblingElement(self, el):
        sib = self.parent[id(el)].children
        i = sib.index(el)
        return sib[i + 1] if i + 1 < len(sib) else None


def make(root):
    u = uia.UIA.__new__(uia.UIA)
    u.walker = Walker(root)
    return u


def test_descends_to_the_only_element_under_the_point():
    button = El("Add to Cart", (10, 10, 50, 30))
    doc = El("doc", (0, 0, 800, 600), [El("header", (0, 0, 800, 5)), El("section", (0, 8, 400, 300), [button])])
    el, ambiguous = make(doc)._descend(doc, 20, 20)
    assert el is button and ambiguous is False


def test_overlapping_siblings_make_the_answer_ambiguous():
    under, overlay = El("under", (0, 0, 100, 100)), El("modal", (0, 0, 300, 300))
    doc = El("doc", (0, 0, 800, 600), [under, overlay])
    el, ambiguous = make(doc)._descend(doc, 20, 20)
    assert ambiguous is True                       # can't tell which one is on top


def test_running_out_of_nodes_gives_no_answer_not_a_partial_guess(monkeypatch):
    monkeypatch.setattr(uia, "DESCEND_MAX_NODES", 3)
    kids = [El(f"k{i}", (0, 0, 10, 10)) for i in range(10)] + [El("target", (15, 15, 30, 30))]
    doc = El("doc", (0, 0, 800, 600), kids)
    assert make(doc)._descend(doc, 20, 20) is None


def test_offscreen_elements_are_ignored():
    hidden, shown = El("hidden", (0, 0, 100, 100), offscreen=True), El("shown", (0, 0, 200, 200))
    doc = El("doc", (0, 0, 800, 600), [hidden, shown])
    el, ambiguous = make(doc)._descend(doc, 20, 20)
    assert el is shown and not ambiguous


def test_focus_lookups_are_answered_before_queued_click_lookups():
    w = uia.UIAWorker(lambda: 0.0)                      # not started: just look at the queue order
    w.submit("point", 0.0, 1, 1)
    w.submit("point", 0.1, 2, 2)
    f = w.submit("focus", 0.2, epoch=1)
    w.stop()
    order = [w.q.get()[2] for _ in range(4)]
    assert order[0][0] == f and order[0][1] == "focus"
    assert [o[1] for o in order[1:3]] == ["point", "point"] and order[3] is None


class _FakeUIA:
    def __init__(self, timeout_ms):
        pass

    def focused(self):
        return {"status": "ok", "role": "edit", "name": "Next page field", "is_password": False}

    def at_point(self, x, y):
        import time
        time.sleep(0.5)                                      # a slow hit test on a busy app
        return {"status": "ok", "role": "button", "name": "Continue"}


def test_a_late_focus_answer_is_not_trusted_even_if_the_epoch_did_not_change(monkeypatch):
    """Review C final H-A: the page may have focused another field by itself (a PIN box after
    navigation); an answer computed long after the keystroke describes that field."""
    import time
    monkeypatch.setattr(uia, "UIA", _FakeUIA)
    w = uia.UIAWorker(time.perf_counter, focus_epoch=lambda: 1)
    w.start()
    w.ready.wait(5)
    late = w.submit("focus", time.perf_counter() - 1.0, epoch=1)   # keystroke a second ago
    assert w.result(late)["status"] == "skipped_stale"
    fresh = w.submit("focus", time.perf_counter(), epoch=1)
    assert w.result(fresh)["status"] == "ok"
    w.stop()


def test_focus_lookups_never_wait_behind_click_lookups(monkeypatch):
    import time
    monkeypatch.setattr(uia, "UIA", _FakeUIA)
    pool = uia.UIAPool(time.perf_counter, focus_epoch=lambda: 1)
    pool.start()
    assert pool.ready.wait(5) and pool.available
    clicks = [pool.submit("point", time.perf_counter(), 1, 1) for _ in range(3)]   # 1.5 s of slow hit tests
    f = pool.submit("focus", time.perf_counter(), epoch=1)
    t0 = time.perf_counter()
    assert pool.result(f)["status"] == "ok" and time.perf_counter() - t0 < 0.3
    for c in clicks:
        pool.result(c, 3)
    pool.stop()
