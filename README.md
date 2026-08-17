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
| `counties.geojson` | Nebraska's 93 county polygons, from the U.S. Census cartographic boundary files |
| `adjacency.json` | which counties share a border — a derived, committed cache |
| `make_seeds.py` | regenerates `counties.json` |

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
county absent from it is drawn in the map's empty color. Removing a county is
how you empty it. All 93 are currently present — the site has no curated
subset yet, so the starting position is a full map, and narrowing it is a
delete.

**The color is not stored — the seed is.** The map hashes the seed to an
index over its palette, so:

- a county's color is the same on every load and for every visitor;
- to recolor one county by hand, change its seed and reload — no code change;
- to reshuffle the whole map, rerun the generator with a different `--salt`.

The palette itself lives in the *site's* CSS (`styles/theme.css`), not here.
This file never names a color, which is what lets the palette be restyled
without rewriting the data.

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
