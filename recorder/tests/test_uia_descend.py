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
