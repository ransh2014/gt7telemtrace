"""The terminal edition of TRACE (Textual). Start it with `gt7telem tui` or `python -m gt7telem.tui`."""


def main() -> int:
    from .app import main as _main
    return _main()
