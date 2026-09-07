"""
launcher.py -- TRACE entry point. Run this directly: python launcher.py
(Thin wrapper so you don't need `python -m gt7telem.launcher` or to `cd`
into the gt7telem/ folder first -- this file sits next to the gt7telem/
package, and Python automatically puts this script's own directory on
sys.path, so `import gt7telem` resolves without any extra setup.)
"""
from gt7telem.launcher import main

if __name__ == "__main__":
    main()
