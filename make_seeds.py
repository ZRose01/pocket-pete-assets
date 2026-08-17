#!/usr/bin/env python3
"""Generate counties.json — the seed table that colors the map.

Run from the repo root:

    python3 make_seeds.py                       # rebuild counties.json
    python3 make_seeds.py --salt whatever       # reshuffle every county
    python3 make_seeds.py --check               # verify, change nothing
    python3 make_seeds.py --rebuild-adjacency   # needs shapely; see below

A county's color is not stored. What is stored is an integer seed, which
the map hashes into an index over its palette (see hash_index below and
its twin colorIndex() in the site's js/county-map.js). A county's color
is therefore stable across loads and across visitors, and the way to
recolor one by hand is to change its seed.

Seeds are not drawn blindly. Three colors over 93 counties means an
uncoordinated draw puts same-colored counties next to each other about a
third of the time, and since the only thing between two counties is a
white hairline, those pairs merge into blobs and the map reads as noise.
So the color layout is solved first and the seeds are fitted to it
afterwards.

Three colors cannot do this perfectly. County maps are planar graphs, the
four color theorem is four for a reason, and Nebraska's 217 shared
borders leave no 3-coloring without clashes — the annealer below gets to
around 9 of 217, which is the practical floor. Those seams are reported
so the number is never a surprise.
"""

import argparse
import json
import math
import random
import sys
from collections import Counter, defaultdict

GEOJSON = "counties.geojson"
ADJACENCY = "adjacency.json"
OUTPUT = "counties.json"

# Number of colors in the site's palette. The seed is hashed modulo this,
# so it is the one number that MUST agree with the site: js/county-map.js
# counts the --nm-fill-seed-N custom properties it finds in CSS and uses
# that. Changing the palette size recolors every county at once — that is
# inherent to hashing a seed rather than storing a color, and it is worth
# knowing before adding a fourth.
PALETTE_SIZE = 3

# Seeds are five digits, so they stay comfortable to read, retype and
# diff by hand in the JSON.
SEED_MIN = 10000
SEED_MAX = 99999

# Annealing schedule. 40 restarts x 60k steps is a couple of seconds on
# a graph this size and lands on 9 conflicts reliably; more of either
# has not been observed to beat it.
RESTARTS = 40
STEPS = 60000
TEMP_START = 2.0
TEMP_FLOOR = 0.01

# A shared border shorter than this (in degrees, as measured by
# border_length below) is a corner touch, not a border. Nebraska's
# counties are laid out on a grid, so four of them meet at a point in
# many places; the two diagonal pairs at such a point are not neighbours
# in any sense a reader would recognise, and holding the coloring to them
# spends constraints for nothing. The measured values fall either side of
# a wide gap — 14 pairs at ~1e-6 and the next at 5e-4 — so the exact
# threshold inside that gap does not matter.
CORNER_THRESHOLD = 0.0005


def hash_index(seed, n):
    """Seed -> palette index. MUST match colorIndex() in js/county-map.js.

    This is the 'lowbias32' finalizer: a 32-bit avalanche, so seeds that
    sit next to each other land on unrelated colors. A plain `seed % n`
    would be simpler and would agree just as exactly across the two
    languages, but it makes sequential seeds cycle through the palette in
    strict rotation, which shows up on the map as banding.
    """
    h = seed & 0xFFFFFFFF
    h ^= h >> 16
    h = (h * 0x7FEB352D) & 0xFFFFFFFF
    h ^= h >> 15
    h = (h * 0x846CA68B) & 0xFFFFFFFF
    h ^= h >> 16
    return h % n


def load_counties(path):
    """GEOID -> county name, from the geometry file."""
    with open(path) as fh:
        geo = json.load(fh)
    return {
        f["properties"]["GEOID"]: f["properties"]["BASENAME"]
        for f in geo["features"]
    }


def border_length(a, b, eps=1e-6):
    """Rough length of the border shared by two touching polygons.

    Buffering one polygon by eps and intersecting with the other yields
    about `length * eps` of area for a shared border, and about eps^2 —
    nothing — for a corner touch. Dividing back out by eps gives a length
    in degrees, which is all this needs: it is a classifier, not a
    measurement, and it only has to separate 1e-6 from 5e-4.

    The obvious direct approach, `a.boundary.intersection(b.boundary)
    .length`, does not work on this data. Neighbouring counties are
    digitized from the same border but with different vertex counts, and
    GEOS returns a degenerate point-set intersection of zero length for
    pairs that plainly do share a border (Adams/Clay, Adams/Hall). The
    area-based measure is indifferent to how either side is vertexed.
    """
    return a.buffer(eps).intersection(b).area / eps


def build_adjacency(path):
    """Which counties share a border. Requires shapely; run rarely.

    Nebraska's county graph is a fixed fact about the state, so the
    result is committed to adjacency.json and this does not run again
    unless the geometry file is replaced. That is what keeps the everyday
    path — rerolling seeds — free of third-party dependencies.

    Candidate pairs are those at distance zero, which is exact rather
    than a tolerance: these polygons come from the Census cartographic
    boundary files, where a shared border is genuinely coincident
    geometry. Candidates are then filtered by border_length to drop
    corner touches.
    """
    from shapely.geometry import shape  # noqa: PLC0415 — optional dependency
    from shapely.validation import make_valid

    with open(path) as fh:
        geo = json.load(fh)

    polys = {}
    repaired = []
    for feature in geo["features"]:
        geoid = feature["properties"]["GEOID"]
        poly = shape(feature["geometry"])
        if not poly.is_valid:
            # One county in the Census file self-intersects. Left in and
            # repaired rather than treated as an error: make_valid changes
            # nothing a reader would see, and every set operation below
            # raises TopologyException without it.
            poly = make_valid(poly)
            repaired.append(geoid)
        polys[geoid] = poly

    if repaired:
        print("repaired invalid geometry: %s" % ", ".join(sorted(repaired)))

    ids = sorted(polys)
    neighbors = {geoid: set() for geoid in ids}
    corners = 0
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            if polys[a].distance(polys[b]) != 0:
                continue
            if border_length(polys[a], polys[b]) <= CORNER_THRESHOLD:
                corners += 1
                continue
            neighbors[a].add(b)
            neighbors[b].add(a)

    print("dropped %d corner-only touches" % corners)
    return neighbors


def load_adjacency(path):
    with open(path) as fh:
        return {geoid: set(v) for geoid, v in json.load(fh).items()}


def save_adjacency(path, neighbors):
    payload = {geoid: sorted(v) for geoid, v in sorted(neighbors.items())}
    with open(path, "w") as fh:
        fh.write(json.dumps(payload, indent=1) + "\n")


def edges_of(neighbors):
    """Each adjacency once, as sorted pairs."""
    return sorted({(min(a, b), max(a, b))
                   for a, ns in neighbors.items() for b in ns})


def conflicts_of(colors, edges):
    """The borders whose two sides ended up the same color."""
    return [(a, b) for a, b in edges
            if a in colors and b in colors and colors[a] == colors[b]]


def solve_colors(ids, neighbors, edges, salt):
    """A 3-coloring with as few same-colored borders as possible.

    Simulated annealing with restarts, rather than the greedy
    most-constrained-first pass this started as: greedy paints itself
    into a corner on a graph this dense and leaves around 49 clashing
    borders, where annealing finds 9. The whole run is driven from one
    RNG seeded with the salt, so a given salt always produces the same
    map.

    Colors are solved here in the abstract; seeds that hash to them are
    fitted afterwards, in fit_seeds. Searching seed-space directly was
    the original mistake — it conflates 'which color goes here' with
    'which number happens to hash to it' and makes the search enormously
    harder for no benefit.
    """
    rng = random.Random(salt)
    best_colors, best_cost = None, None

    for _ in range(RESTARTS):
        colors = {g: rng.randrange(PALETTE_SIZE) for g in ids}
        for step in range(STEPS):
            temp = TEMP_START * (1 - step / STEPS) + TEMP_FLOOR
            geoid = ids[rng.randrange(len(ids))]
            old = colors[geoid]
            new = rng.randrange(PALETTE_SIZE)
            if new == old:
                continue
            # Only this county's own borders can change cost.
            delta = (sum(1 for n in neighbors[geoid] if colors[n] == new)
                     - sum(1 for n in neighbors[geoid] if colors[n] == old))
            if delta <= 0 or rng.random() < math.exp(-delta / temp):
                colors[geoid] = new

        cost = len(conflicts_of(colors, edges))
        if best_cost is None or cost < best_cost:
            best_colors, best_cost = dict(colors), cost
            if cost == 0:
                break

    return best_colors


def fit_seeds(ids, colors, salt):
    """A seed per county that hashes to the color solved for it.

    The hash spreads evenly over the palette, so a draw lands on the
    wanted color about one time in PALETTE_SIZE and this terminates
    quickly. Drawn from an RNG seeded with the salt, so the file is a
    pure function of the salt.
    """
    rng = random.Random("seeds:" + salt)
    chosen = {}
    for geoid in ids:
        while True:
            candidate = rng.randint(SEED_MIN, SEED_MAX)
            if hash_index(candidate, PALETTE_SIZE) == colors[geoid]:
                chosen[geoid] = candidate
                break
    return chosen


def build(names, chosen):
    """The published shape. Sorted by GEOID so diffs stay readable."""
    return {
        "version": 1,
        "counties": {
            geoid: {"name": names[geoid], "seed": chosen[geoid]}
            for geoid in sorted(chosen)
        },
    }


def report(names, edges, colors):
    """One conflict number, computed one way.

    This used to be counted twice — once as 'counties the generator could
    not place cleanly' and once as 'counties with a same-colored
    neighbour' — which are different measures that printed different
    numbers for the same map. There is now a single measure: borders
    whose two sides match.
    """
    spread = Counter(colors.values())
    clashes = conflicts_of(colors, edges)
    print("counties:       %d" % len(colors))
    print("shared borders: %d" % len(edges))
    print("palette spread: %s" % dict(sorted(spread.items())))
    if clashes:
        listed = ", ".join("%s/%s" % (names[a], names[b]) for a, b in clashes)
        print("same-colored borders: %d of %d (%s)"
              % (len(clashes), len(edges), listed))
    else:
        print("same-colored borders: none")
    return clashes


def check(names, committed):
    """Compare the committed table against the geometry.

    Seeds are not regenerated and not compared: hand-editing one is the
    supported way to recolor a county, and rerunning the generator would
    wipe it. What this catches is drift between the two files — a county
    missing from the table, a stale name, an id that is not Nebraska.
    """
    problems = []
    entries = committed.get("counties", {})
    for geoid, name in sorted(names.items()):
        entry = entries.get(geoid)
        if entry is None:
            problems.append("%s (%s) missing from the table" % (geoid, name))
        elif entry.get("name") != name:
            problems.append("%s name is %r, geometry says %r"
                            % (geoid, entry.get("name"), name))
        elif not isinstance(entry.get("seed"), int):
            problems.append("%s (%s) has a non-integer seed" % (geoid, name))
    for geoid in sorted(entries):
        if geoid not in names:
            problems.append("%s is not a Nebraska county" % geoid)
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--salt", default="pocket-pete-2026",
        help="reshuffles every seed; the same salt always gives the same file",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="verify the committed counties.json instead of writing it",
    )
    parser.add_argument(
        "--rebuild-adjacency", action="store_true",
        help="recompute adjacency.json from the geometry (requires shapely)",
    )
    args = parser.parse_args()

    names = load_counties(GEOJSON)

    if args.rebuild_adjacency:
        neighbors = build_adjacency(GEOJSON)
        save_adjacency(ADJACENCY, neighbors)
        print("wrote %s" % ADJACENCY)
    else:
        neighbors = load_adjacency(ADJACENCY)

    edges = edges_of(neighbors)

    if args.check:
        with open(OUTPUT) as fh:
            committed = json.load(fh)
        colors = {
            geoid: hash_index(entry["seed"], PALETTE_SIZE)
            for geoid, entry in committed.get("counties", {}).items()
            if isinstance(entry.get("seed"), int)
        }
        report(names, edges, colors)
        problems = check(names, committed)
        if problems:
            print("\nFAIL")
            for problem in problems:
                print("  " + problem)
            return 1
        print("\nOK — %d counties, all present and named correctly"
              % len(committed.get("counties", {})))
        return 0

    ids = sorted(names)
    colors = solve_colors(ids, neighbors, edges, args.salt)
    chosen = fit_seeds(ids, colors, args.salt)

    # Report the colors the seeds actually produce, not the ones the
    # solver decided on, so a mismatch between hash_index here and the
    # fitting above could never hide behind an optimistic report.
    report(names, edges, {g: hash_index(chosen[g], PALETTE_SIZE) for g in ids})

    with open(OUTPUT, "w") as fh:
        fh.write(json.dumps(build(names, chosen), indent=2) + "\n")
    print("\nwrote %s" % OUTPUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
