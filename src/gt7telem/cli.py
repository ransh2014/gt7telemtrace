"""gt7telem-tools: the 1.2.0 analysis tools from the command line, on saved lap files.

    gt7telem-tools coach LAP.json REF.json          corner-by-corner tips
    gt7telem-tools setup LAP.json                   understeer / oversteer / wheelspin / bottoming + fixes
    gt7telem-tools ab BEFORE_DIR AFTER_DIR          did the setup change help? (laps in two folders)
    gt7telem-tools plan LAPS_DIR                    practice plan from a session's laps
    gt7telem-tools validate LAP.json [--ref-length M]
    gt7telem-tools replay LAP.json OUT.html [--ghost REF.json] [--gif OUT.gif]
    gt7telem-tools dna LAP.json [--save] [--code] [--vs CODE]
    gt7telem-tools dyno-compare A.json B.json
    gt7telem-tools challenge new|add|show|export|import ...

A lap file is what the Dashboard saves: a JSON object with `samples` and `lap_time_s`.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path


def _load(path):
    with open(path, encoding="utf-8-sig") as f:
        d = json.load(f)
    if not isinstance(d, dict) or not isinstance(d.get("samples"), list):
        raise ValueError(f"{path} is not a TRACE lap file")
    return d


def _frame(data):
    from . import corners

    df = corners.frame_from_samples(data["samples"])
    if df is None:
        raise ValueError("this lap has no usable samples")
    return df


def _folder(path):
    out = []
    for p in sorted(Path(path).glob("*.json")):
        try:
            d = _load(p)
            out.append((_frame(d), float(d.get("lap_time_s") or 0)))
        except (OSError, ValueError, KeyError):
            continue
    return out


def cmd_coach(a):
    from . import coach

    tips = coach.analyse(_frame(_load(a.lap)), _frame(_load(a.ref)), a.n)
    print(coach.describe(tips))


def cmd_setup(a):
    from . import setup_advisor

    print(setup_advisor.describe(setup_advisor.diagnose(_frame(_load(a.lap)))))


def cmd_ab(a):
    from . import setup_advisor

    before, after = _folder(a.before), _folder(a.after)
    r = setup_advisor.compare_ab([f for f, _ in before], [f for f, _ in after],
                                 [t for _, t in before], [t for _, t in after])
    print(r["text"])


def cmd_plan(a):
    from . import planner

    laps = _folder(a.folder)
    r = planner.plan([f for f, _ in laps], [t for _, t in laps], a.n)
    print(r["text"])


def cmd_validate(a):
    from . import validate

    d = _load(a.lap)
    res = validate.check(_frame(d), a.ref_length, float(d.get("lap_time_s") or 0) or None)
    print(validate.describe(res))
    return 0 if res["verdict"] == "clean" else 1


def cmd_replay(a):
    from . import replay3d

    d = _load(a.lap)
    ghost = _load(a.ghost)["samples"] if a.ghost else None
    if not replay3d.usable(d["samples"]):
        print("This lap has no position data to replay.")
        return 1
    replay3d.write_html(a.out, d["samples"], title=f"{d.get('car', 'Lap')} - {d.get('track', '')}".strip(" -"),
                        ghost_samples=ghost)
    print(f"Wrote {a.out}")
    if a.gif:
        print(f"Wrote {a.gif}" if replay3d.export_gif(a.gif, d["samples"]) else "GIF needs Pillow; skipped.")


def cmd_dna(a):
    from . import driver_dna as dna

    p = dna.profile(_frame(_load(a.lap)))
    if p is None:
        print("This lap can't be read.")
        return 1
    print(dna.archetype(p))
    for k in dna.TRAITS:
        print(f"  {dna.LABELS[k]:<20} {'--' if p[k] is None else f'{p[k]:.0f}'}")
    if a.save:
        dna.record(p, _dt.date.today().isoformat())
    if a.code:
        print(dna.export_code(p, a.name))
    if a.vs:
        other = dna.import_code(a.vs)
        if other is None:
            print("That DNA code isn't valid.")
            return 1
        print(f"\nYou vs {other[0]}:")
        print("\n".join(dna.compare(p, other[1], "You", other[0])))


def cmd_dyno_compare(a):
    from . import dyno, dyno_compare

    fa, fb = _frame(_load(a.a)), _frame(_load(a.b))
    r = dyno_compare.overlay(dyno.analyse(fa), dyno.analyse(fb), Path(a.a).stem, Path(a.b).stem)
    print(r["text"])
    for path, df in ((a.a, fa), (a.b, fb)):
        adv = dyno_compare.final_drive_advice(dyno.analyse(df), df["speed_kmh"])
        print(f"{Path(path).stem}: {adv['text']}")


def cmd_challenge(a):
    from . import groups

    board = groups.load()
    ch = next((c for c in board if c["week"] == (a.week or groups.week_id())
               and c["track"].lower() == (a.track or "").lower() and c["car"].lower() == (a.car or "").lower()), None)
    if a.action == "import":
        ch, msg = groups.import_code(a.code, ch)
        print(msg)
        if ch is not None and ch not in board:
            board.append(ch)
        groups.save(board)
        return 0
    if a.action == "new":
        if ch is None:
            ch = groups.make_challenge(a.track, a.car, a.week)
            board.append(ch)
            groups.save(board)
        print(f"Challenge ready: {ch['name']} ({ch['week']})")
        return 0
    if ch is None:
        print("No such challenge. Create it with: challenge new --track T --car C")
        return 1
    if a.action == "add":
        d = _load(a.lap)
        from . import theoretical

        secs = [int(round(x * 1000)) for x in theoretical.sector_times(_frame(d), 3)]
        ok = groups.add_result(ch, a.player, round(float(d["lap_time_s"]) * 1000), secs)
        groups.save(board)
        print("Added." if ok else "Rejected (bad time or name).")
    elif a.action == "export":
        print(groups.export_result(ch, a.player) if a.player else groups.export_challenge(ch))
    for r in groups.standings(ch):
        print(f"{r['pos']:>2}. {r['player']:<24} {groups.fmt(r['time_ms'])}  +{r['gap_ms'] / 1000:.3f}")
    ideal = groups.ideal_lap(ch)
    if ideal:
        print(f"Group ideal lap: {groups.fmt(ideal)}")
    return 0


def build_parser():
    ap = argparse.ArgumentParser(prog="gt7telem-tools", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, fn, *args, **opts):
        sp = sub.add_parser(name)
        for flag, kw in args:
            sp.add_argument(flag, **kw)
        sp.set_defaults(fn=fn)
        return sp

    add("coach", cmd_coach, ("lap", {}), ("ref", {}), ("-n", {"type": int, "default": 3}))
    add("setup", cmd_setup, ("lap", {}))
    add("ab", cmd_ab, ("before", {}), ("after", {}))
    add("plan", cmd_plan, ("folder", {}), ("-n", {"type": int, "default": 3}))
    add("validate", cmd_validate, ("lap", {}), ("--ref-length", {"type": float}))
    add("replay", cmd_replay, ("lap", {}), ("out", {}), ("--ghost", {}), ("--gif", {}))
    add("dna", cmd_dna, ("lap", {}), ("--save", {"action": "store_true"}), ("--code", {"action": "store_true"}),
        ("--name", {"default": ""}), ("--vs", {}))
    add("dyno-compare", cmd_dyno_compare, ("a", {}), ("b", {}))
    add("challenge", cmd_challenge, ("action", {"choices": ["new", "add", "show", "export", "import"]}),
        ("--track", {}), ("--car", {}), ("--week", {}), ("--player", {"default": ""}), ("--lap", {}), ("--code", {}))
    return ap


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    try:
        return a.fn(a) or 0
    except (OSError, ValueError, KeyError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
