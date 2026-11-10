"""The UI-free core of TRACE: what the Tk windows and the terminal UI both render.

* registry.py  -- the one list of screens, chart groups, tools and settings both front ends build their menus from
* charts.py    -- run a chart group headlessly and get a plain-data spec back (figspec.py extracts it)
* figspec.py   -- matplotlib Figure -> plain data

Nothing in here imports tkinter.
"""
