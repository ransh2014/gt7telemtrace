"""The scroll helpers build real Tk widgets; skipped where there is no display."""
import pytest

tk = pytest.importorskip("tkinter")


@pytest.fixture
def root():
    try:
        r = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    r.geometry("500x300")
    yield r
    r.destroy()


def test_scroll_view_keeps_minimum_size_and_scrolls(root):
    from gt7telem import uiscroll
    inner = uiscroll.scroll_view(root, "#000000", 900, 500)
    root.update()
    assert inner.winfo_width() == 900 and inner.winfo_height() == 500   # window is smaller: it scrolls
    root.geometry("1100x640")
    root.update()
    assert inner.winfo_width() >= 1000 and inner.winfo_height() >= 560  # window is bigger: it fills


def test_tab_strip_follows_the_notebook(root):
    from tkinter import ttk

    from gt7telem import uiscroll
    nb = ttk.Notebook(root, style="Plain.TNotebook")
    nb.pack(fill="both", expand=True)
    names = [f"Group {i}" for i in range(20)]
    for n in names:
        nb.add(tk.Frame(nb), text=n)
    wrap = uiscroll.tab_strip(root, nb, names, "#000", "#111", "#888", "#222", "#0ff", ("TkDefaultFont", 9))
    root.update()
    assert wrap.winfo_exists()
    nb.select(15)
    root.update()
    assert nb.index("current") == 15
