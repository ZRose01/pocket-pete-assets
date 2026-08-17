#!/usr/bin/env python3
"""Generate counties.synthetic.json — a stand-in database, for looking at.

    python3 make_synthetic.py              # rebuild counties.synthetic.json
    python3 make_synthetic.py --salt foo   # a different arrangement
    python3 make_synthetic.py --fill 45    # how many counties to fill

THIS FILE IS NOT DATA. It exists so the map can be judged — a partially
filled map is the thing the design has to work for, and the real table
(counties.json) currently lists all 93 counties, which means the empty
state never appears and the layout is never tested against a hole. Delete
it, and the site's COUNTY_DB pointer, once a real table exists.

Everything about the shape of the output is identical to counties.json,
so swapping between them is a one-line change on the site and nothing
downstream can tell the difference.

Why the filled counties are not simply picked at random: real coverage
data clusters. Somebody works a region, so the filled counties come in
contiguous runs with empty stretches between them, and a uniform scatter
of 40 counties over 93 produces a speckle that looks nothing like it —
and, more to the point, understates how often two filled counties end up
adjacent, which is exactly the case the palette has to survive. So this
grows clusters outward from a handful of centers instead.

The centers are Nebraska's population counties, which is where anything
real would start. They are named rather than given by FIPS, and resolved
against the geometry, so a typo fails loudly instead of quietly seeding a
cluster in the wrong place.
"""

import argparse
import json
import random
import sys

import make_seeds as seeds

OUTPUT = "counties.synthetic.json"

# Where coverage would plausibly begin: the metro counties, the Platte
# valley towns, the panhandle seat. Any of these missing from the
# geometry is an error, not a warning — see resolve_centers.
CENTER_NAMES = [
    "Douglas",       # Omaha
    "Lancaster",     # Lincoln
    "Sarpy",         # Bellevue / Papillion
    "Hall",          # Grand Island
    "Buffalo",       # Kearney
    "Scotts Bluff",  # Scottsbluff
    "Madison",       # Norfolk
    "Lincoln",       # North Platte
    "Adams",         # Hastings
    "Dodge",         # Fremont
]

# How many counties end up filled, of 93. A bit under half: enough that
# the pastels carry the map, sparse enough that the transparent counties
# are a real presence rather than a few gaps.
DEFAULT_FILL = 40

def resolve_centers(names_by_geoid):
    """Center county names -> GEOIDs, loudly."""
    by_name = {name: geoid for geoid, name in names_by_geoid.items()}
    missing = [n for n in CENTER_NAMES if n not in by_name]
    if missing:
        raise SystemExit(
            "center counties not found in %s: %s"
            % (seeds.GEOJSON, ", ".join(missing))
        )
    return [by_name[n] for n in CENTER_NAMES]


def grow(centers, neighbors, target, rng):
    """Pick `target` counties as clusters spreading from the centers.

    Every center is taken first, so each one is guaranteed to appear —
    an earlier version interleaved them with growth and quietly dropped
    North Platte when the budget ran out before its turn, which is the
    kind of silent shortfall a stand-in dataset should not have. If
    target is smaller than the center list, the centers are simply
    truncated.

    Growth then draws uniformly from the frontier: every county adjacent
    to something already filled. Uniform over the frontier *set* is not
    uniform over the clusters — a big cluster contributes more frontier
    and so keeps growing faster — which is the rich-get-richer behaviour
    real coverage has, with a heavily worked metro and a lone county out
    west, and it comes out of the draw rather than out of a weighting
    rule.

    Falls back to any unfilled county if the frontier empties, so the
    target is always reached even on a graph narrowed by hand.
    """
    filled = set()
    frontier = set()

    def take(geoid):
        filled.add(geoid)
        frontier.discard(geoid)
        for n in neighbors.get(geoid, ()):
            if n not in filled:
                frontier.add(n)

    for geoid in centers[:target]:
        take(geoid)

    everything = sorted(neighbors)
    while len(filled) < target:
        if frontier:
            take(rng.choice(sorted(frontier)))
            continue
        left = [g for g in everything if g not in filled]
        if not left:
            break
        take(rng.choice(left))

    return filled


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--salt", default="synthetic-2026",
                        help="reshuffles both the selection and the colors")
    parser.add_argument("--fill", type=int, default=DEFAULT_FILL,
                        help="how many of the 93 counties to fill")
    args = parser.parse_args()

    names = seeds.load_counties(seeds.GEOJSON)
    neighbors = seeds.load_adjacency(seeds.ADJACENCY)

    if not 1 <= args.fill <= len(names):
        raise SystemExit("--fill must be between 1 and %d" % len(names))

    rng = random.Random("pick:" + args.salt)
    centers = resolve_centers(names)
    filled = sorted(grow(centers, neighbors, args.fill, rng))

    # Color only the counties that are filled, and only against the
    # borders between two filled counties. A border to an unfilled county
    # constrains nothing — there is no color on the other side of it —
    # and holding the solver to those borders would spend its effort on
    # seams no visitor can see.
    subgraph = {g: {n for n in neighbors.get(g, ()) if n in set(filled)}
                for g in filled}
    edges = seeds.edges_of(subgraph)

    colors = seeds.solve_colors(filled, subgraph, edges, args.salt)
    chosen = seeds.fit_seeds(filled, colors, args.salt)

    actual = {g: seeds.hash_index(chosen[g], seeds.PALETTE_SIZE) for g in filled}
    print("filled:  %d of %d counties" % (len(filled), len(names)))
    seeds.report(names, edges, actual)

    payload = seeds.build(names, chosen)
    with open(OUTPUT, "w") as fh:
        fh.write(json.dumps(payload, indent=2) + "\n")
    print("\nwrote %s" % OUTPUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
