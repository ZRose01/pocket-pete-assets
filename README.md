# pocket-pete-assets

The static data behind the county map on **pocketPeteRicketts.com**. Served
straight off GitHub Pages — no API, no build on the consuming side, and no
redeploy of the site needed to change what the map shows.

```
https://zrose01.github.io/pocket-pete-assets/
```

Sibling to `ZRose01/o4s-map-assets`, which does the same job for the Osborn
sites. This is a **separate** repo on purpose: that one is an event feed
written by the campaign's Mobilize sync, and nothing here should ever be
overwritten by it.

| file | what it is |
|---|---|
| `counties.json` | **the database.** Which counties are filled in, and the seed that colors each one |
| `counties.synthetic.json` | a **stand-in** table, 40 counties, for looking at the design — see below |
| `counties.geojson` | Nebraska's 93 county polygons, from the U.S. Census cartographic boundary files |
| `adjacency.json` | which counties share a border — a derived, committed cache |
| `make_seeds.py` | regenerates `counties.json` |
| `make_synthetic.py` | regenerates `counties.synthetic.json` |

## counties.json

```json
{
  "version": 1,
  "counties": {
    "31055": { "name": "Douglas", "seed": 62934 },
    "31109": { "name": "Lancaster", "seed": 31583 }
  }
}
```

Keyed by county FIPS (`GEOID` in the geojson), so the key is the identity and
a duplicate is impossible to write.

**Presence is the fill.** A county in this table is drawn in its color; a
county absent from it is drawn as a bare outline, with the page's own
background showing through. Removing a county is how you empty it. All 93 are
currently present in `counties.json` — there is no curated subset yet, so the
starting position is a full map and narrowing it is a delete. (Which is why
`counties.synthetic.json` exists: with every county filled, the empty
treatment is never on screen.)

**The color is not stored — the seed is.** The map hashes the seed to an
index over its palette, so:

- a county's color is the same on every load and for every visitor;
- to recolor one county by hand, change its seed and reload — no code change;
- to reshuffle the whole map, rerun the generator with a different `--salt`.

The palette itself lives in the *site's* CSS (`styles/theme.css`), not here.
This file never names a color, which is what lets the palette be restyled
without rewriting the data.

## counties.synthetic.json — not data

A stand-in, so the map can be judged before there is anything real to put
in it. `counties.json` lists all 93 counties, which means no county is ever
empty; the site's whole unfilled treatment — counties drawn as bare outlines
over the page's blue — never appears, and the layout is never seen against a
hole. This fills 40.

**It is identical in shape to `counties.json`**, so the site swaps between
them with a one-line change (`COUNTY_DB` in its `index.html`), and nothing
downstream can tell the difference. Delete both this file and that pointer
once a real table exists.

```bash
python3 make_synthetic.py              # rebuild it
python3 make_synthetic.py --fill 55    # a fuller map
python3 make_synthetic.py --salt foo   # a different arrangement
```

The filled counties are **clusters grown from Nebraska's population
counties**, not a random scatter. Real coverage data clusters — somebody
works a region — so it comes in contiguous runs with empty stretches
between, and a uniform scatter of 40 over 93 both looks wrong and understates
how often two filled counties end up adjacent, which is exactly the case the
palette has to survive.

Every center county is taken before growth starts, so each is guaranteed to
appear. An earlier version interleaved them with growth and quietly dropped
North Platte when the fill budget ran out before its turn. Growth then draws
uniformly from the frontier — which is *not* uniform over clusters, since a
big cluster contributes more frontier and so keeps growing faster. That
rich-get-richer unevenness is what real coverage looks like, and it falls out
of the draw rather than out of a weighting rule.

Colors are solved over the filled subgraph only. A border to an unfilled
county constrains nothing — there is no color on the other side of it — so
holding the solver to those borders would spend its effort on seams no
visitor can see. With only 58 borders between filled counties, three colors
are comfortably enough and it finds a clean coloring:

```
filled:  40 of 93 counties
shared borders: 58
palette spread: {0: 11, 1: 15, 2: 14}
same-colored borders: none
```

## Regenerating

```bash
python3 make_seeds.py                     # rebuild counties.json
python3 make_seeds.py --salt some-string  # a different arrangement
python3 make_seeds.py --check             # verify against the geometry, write nothing
```

Standard library only. The one exception is `--rebuild-adjacency`, which needs
`shapely` (`pip install shapely`) and only has to run if `counties.geojson` is
ever replaced — county borders are not news.

`--check` compares the table against the geometry: every county present, names
matching, seeds integral, no ids that are not Nebraska. It deliberately does
**not** compare seeds, because hand-editing a seed is a supported edit and
regenerating would wipe it.

### Why the seeds aren't random

Three colors over 93 counties, drawn independently, put same-colored counties
next to each other about a third of the time. The only thing between two
counties on the map is a white hairline, so those pairs merge into blobs and
the map reads as noise rather than as a design.

So `make_seeds.py` solves the color layout first — simulated annealing over
the adjacency graph, minimizing borders whose two sides match — and only then
fits a seed to each county that hashes to the color it was given. Searching
seed-space directly was the first attempt and is much worse: it conflates
"which color belongs here" with "which number happens to hash to it".

**It cannot be perfect.** A county map is a planar graph and the four color
theorem is four for a reason; Nebraska's 217 shared borders admit no clean
3-coloring. The generator reports what it left:

```
counties:       93
shared borders: 217
palette spread: {0: 32, 1: 31, 2: 30}
same-colored borders: 9 of 217 (Blaine/Loup, Boone/Greeley, Butler/Platte,
  Cherry/Hooker, Custer/Lincoln, Hamilton/Merrick, Hayes/Lincoln,
  Keith/McPherson, Morrill/Sheridan)
```

Nine seams out of 217 is the practical floor for three colors here. A
different `--salt` moves them around; it does not remove them.

### adjacency.json

Which counties share a border, committed rather than recomputed because
Nebraska's county graph is a fixed fact and because computing it is the only
step that needs a third-party library.

Two counties are candidates when their polygons are at distance **zero** —
exact, not a tolerance, since the Census files digitize a shared border as
genuinely coincident geometry. Candidates are then filtered by the length of
that shared border, which drops 14 corner-only touches: Nebraska's counties
sit on a grid, four of them meet at a point in many places, and the two
diagonal pairs at such a point are not neighbours in any sense a reader would
recognise. 231 touching pairs, 217 real borders.

Two traps are worth recording, because both produced confidently wrong graphs
before being caught:

- **Shared *vertices* do not work.** Neighbouring counties are digitized from
  the same border but with different vertex counts, so matching coordinate
  pairs finds only a fraction of the graph — it gave Douglas two neighbours
  (it has four) and Hall one (it has five), and 115 pairs instead of 217.
- **`boundary.intersection(boundary).length` does not work either**, for the
  same reason: GEOS returns a degenerate zero-length intersection for pairs
  that plainly share a border, including Adams/Clay and Adams/Hall. Border
  length is measured by area instead — buffer one side by epsilon, intersect
  with the other, divide the area back out — which is indifferent to how
  either side is vertexed.

One county polygon (`31153`, Sarpy) is self-intersecting in the Census file
and is repaired with `make_valid` on load; without it every set operation
raises `TopologyException`.

## The hash

`hash_index()` in `make_seeds.py` and `colorIndex()` in the site's
`js/county-map.js` **must stay identical** — they are the same function in two
languages, and if they disagree the map's colors stop matching everything this
repo reports. It is the `lowbias32` finalizer, a 32-bit avalanche, chosen over
a plain `seed % 3` so that adjacent seeds land on unrelated colors instead of
cycling through the palette in strict rotation (which shows up as banding).

`PALETTE_SIZE` is the other coupling: it must equal the number of seeded
colors the site defines. Changing it recolors every county at once. That is
inherent to hashing a seed rather than storing a color, and is the price of
being able to restyle the palette without touching the data.
