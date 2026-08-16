"""One army's row of a payoff matrix, with intervals. Reads arena --out files.

Every drafted comparison in this project ends the same way: take one army,
score it against each other army in the file, and read the row. That was being
hand-written as a throwaway snippet each time, which is how two different
sigma conventions ended up in the same conversation.

    python3 rowscore.py campaigns/field_plan.json
    python3 rowscore.py campaigns/field_plan.json --row 0 --vs campaigns/field_search.json

With --vs, the two files are compared cell by cell for the same row, which is
the paired form: the opponent and the game index are identical in both, so the
difference isolates whatever changed between the runs.

READ THE EFFECTIVE-SAMPLE LINE. A drafted cell whose drafters are
deterministic produces ONE handoff position, replayed with node jitter, so its
interval describes the referee and not the drafting. This prints the number of
distinct positions behind each row when it can tell.
"""

import argparse
import json
import math


def draft_label(meta):
    """Human label for the draft config, across BOTH file shapes.

    arena writes a list -- [white_mode, black_mode, depth, width, ...] -- and
    expand writes a plain True, because a breeding campaign has one mode on
    both sides by construction. Assuming arena's shape crashed on every
    campaign file.
    """
    d = meta.get("draft")
    if not d:
        return "stamped"
    if isinstance(d, (list, tuple)):
        return "drafted %s" % (list(d[:2]),)
    return "drafted"


def load(path):
    with open(path) as fh:
        state = json.load(fh)
    cells = {}
    for k, v in state["cells"].items():
        i, j, g = (int(x) for x in k.split(","))
        cells[(i, j, g)] = v
    return cells, state.get("meta", {})


def row(cells, n, i):
    """{opponent: [scores from i's side]} using both colours of each pair."""
    out = {}
    for j in range(n):
        if j == i:
            continue
        vals = [v for (a, b, g), v in cells.items()
                if a == i and b == j and v is not None]
        vals += [1.0 - v for (a, b, g), v in cells.items()
                 if a == j and b == i and v is not None]
        if vals:
            out[j] = vals
    return out


def ci(vals):
    n = len(vals)
    m = sum(vals) / n
    if n < 2:
        return m, float("nan")
    var = sum((v - m) ** 2 for v in vals) / (n - 1)
    return m, 1.96 * math.sqrt(var / n)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("matrix")
    ap.add_argument("--row", type=int, default=0, help="army index (default 0)")
    ap.add_argument("--vs", help="second matrix to pair against, cell by cell")
    ap.add_argument("--maximin", metavar="I,J,K",
                    help="rank EVERY other army by its worst score against "
                         "these columns. This is the shipping rule: the army "
                         "with the best worst column wins, and dominance "
                         "breaks ties.")
    args = ap.parse_args()

    cells, meta = load(args.matrix)
    n = len(meta.get("armies", [])) or (max(max(k[0], k[1])
                                            for k in cells) + 1)

    if args.maximin:
        field = [int(x) for x in args.maximin.split(",")]
        fps = meta.get("armies", [])
        draft = meta.get("draft")
        print("%s  maximin over columns %s  %s"
              % (args.matrix, field, draft_label(meta)))
        table = []
        for i in range(n):
            if i in field:
                continue
            ri = row(cells, n, i)
            got = {j: ci(ri[j]) for j in field if j in ri}
            if len(got) != len(field):
                continue          # incomplete row, cannot be ranked honestly
            worst = min(m for m, _ in got.values())
            table.append((worst, i, got))
        if not table:
            raise SystemExit("no army has a complete row over %s" % field)
        table.sort(reverse=True)
        head = "  %-4s %-18s" % ("army", "fingerprint")
        print(head + "".join("  vs %-9d" % j for j in field) + "  WORST")
        for worst, i, got in table:
            fp = fps[i][:16] if i < len(fps) else ""
            print("  %-4d %-18s" % (i, fp)
                  + "".join("  %.4f     " % got[j][0] for j in field)
                  + "  %.4f" % worst)
        best = table[0]
        print()
        print("  maximin pick: army %d, worst column %.4f" % (best[1], best[0]))
        if best[0] < 0.5:
            print("  NOTE: below 0.5 -- the best available army still LOSES to "
                  "its worst opponent.")
        print("  effective drafting sample: %d matchups (deterministic drafters "
              "give one position each)" % (len(table) * len(field)))
        return

    r = row(cells, n, args.row)
    if not r:
        raise SystemExit("no cells for row %d in %s" % (args.row, args.matrix))

    draft = meta.get("draft")
    print("%s  row %d  %s" % (args.matrix, args.row, draft_label(meta)))
    allv = []
    for j in sorted(r):
        m, e = ci(r[j])
        allv += r[j]
        print("  vs %-3d  %.4f +/- %.4f   (%d)" % (j, m, e, len(r[j])))
    m, e = ci(allv)
    print("  ROW     %.4f +/- %.4f   (%d pair-games over %d opponents)"
          % (m, e, len(allv), len(r)))
    if draft:
        print("  effective drafting sample: %d distinct matchups. Both drafters "
              "are deterministic, so each contributes ONE position replayed "
              "with node jitter -- the interval above is the referee's noise, "
              "not the drafting's." % len(r))

    if args.vs:
        cells2, meta2 = load(args.vs)
        d2 = meta2.get("draft")
        paired = []
        for k, v in cells.items():
            a, b, _g = k
            if v is None or k not in cells2 or cells2[k] is None:
                continue
            if a == b:
                # the diagonal is the army against ITSELF, where "row 0's
                # score" is meaningless and is 0.5 by construction. Including
                # it just dilutes the difference toward zero.
                continue
            if a == args.row:
                paired.append(cells2[k] - v)
            elif b == args.row:
                paired.append(v - cells2[k])
        if not paired:
            raise SystemExit("no cells in common between the two matrices")
        m, e = ci(paired)
        print()
        print("paired difference, %s minus %s, for row %d"
              % (args.vs, args.matrix, args.row))
        print("  %+.4f +/- %.4f over %d shared cells" % (m, e, len(paired)))
        print("  %s" % ("EXCLUDES zero" if abs(m) > e and not math.isnan(e)
                        else "includes zero: no difference established"))
        if draft or d2:
            print("  arms: %s vs %s" % (draft_label(meta), draft_label(meta2)))


if __name__ == "__main__":
    main()
