"""Popular/common names for catalog objects (e.g. "NGC6888" -> "Crescent Nebula") --
per explicit user request, so a well-known object shows the name people actually call
it, not just its catalog designation. Previously only Messier objects had this, and
only because Siril's own bundled messier.csv happens to bundle a common name in its own
"alias" column -- every other catalog (NGC, IC, Sh2, Barnard, RCW, Arp, Hickson) had no
such data anywhere in this app at all.

Source: Wikidata (CC0 1.0 -- public domain dedication, no attribution or share-alike
requirement), queried via its own SPARQL endpoint (query.wikidata.org/sparql) against
the P528 ("catalog code") statement type, scoped per catalog by that statement's P972
("catalog") qualifier. Two other sources were considered and rejected: Stellarium's own
bundled nebula name list has excellent coverage but ships under Stellarium's GPL-2.0,
which is not compatible with bundling into this MIT-licensed project; OpenNGC (CC-BY-
SA-4.0, friendlier but still copyleft) only covers NGC/IC, missing every other catalog
here entirely. Retrieved 2026-09-03.

Building this required real data cleaning, not just a straight dump of query results --
Wikidata's P528 statements are noisy for this purpose: most "different from the code"
labels turned out to be a *different* cross-referenced catalog code restated (e.g. NGC
1002's own P528 statement pairs it with "NGC 983", not a name), a star or sub-component
*within* the object rather than the object itself (star/cluster-member designations
carry a trailing component number in the code, e.g. "NGC 104 37"), or a spelled-out
version of the very same designation ("Barnard 147" for B147). Kept only entries where:
the underlying P528 code is a clean, bare designation (no trailing component number);
the label isn't itself shaped like some other catalog's own code (a single capitalized
word plus a bare number -- "Berkeley 50", "Westerhout 40", "Ruprecht 147" -- almost
always is); and the label isn't a Bayer/Flamsteed-style variable-star name ("19 Puppis",
"TW Horologii"). The two survivors of that filter that still looked doubtful were
individually checked live against SIMBAD before inclusion/exclusion: Sh2-244 really is
a second designation for the Crab Nebula (SIMBAD's own primary ID for it *is* "M 1"),
confirmed correct; IC4816 "Nova Sagittarii 1898" and NGC1664 "4-H cluster" were dropped
-- the former is SIMBAD's entry for a star (V1059 Sgr), not this feature's intended
"named nebula/galaxy" sense, and the latter had no independent corroboration anywhere.

Deliberately conservative in scope as a result: covering only Messier, NGC, IC, Sh2,
Barnard, RCW, Arp, and Hickson -- not every catalog this app supports. LDN, LBN, vdB,
Gum, SNR, Abell, and WR were investigated but Wikidata's P528/P972 structured data for
them either wasn't discoverable (most of these smaller/less mainstream catalogs' own
"catalog" qualifier item couldn't be reliably identified) or, where it was, didn't
survive the same cleaning process. A real, direct object shows no popular name at all
far more often than not (only ~600 of Wikidata's ~14,000 NGC-tagged items even had a
P528-linked label before this filtering ran) -- that's expected and correct, not a gap
in this table.

Per a real report: an object's popular name was showing up under one of its catalog
designations (e.g. "NGC6888" -> "Crescent Nebula") but not under another designation
for the very same object (e.g. "Sh2-105", which the same per-catalog P972-scoped
query missed -- Wikidata isn't internally consistent about which "catalog" qualifier
item a given cross-reference statement carries, and this one apparently used a
different or missing one than the Sh2 query was scoped to). Fixed with a second pass:
for every name already found, resolve its Wikidata item and pull *all* of that item's
other P528 codes (regardless of qualifier), then check each against every supported
catalog's own designation shape to discover siblings the first pass missed. That pass
found exactly two candidates -- both were independently checked against SIMBAD before
deciding, same bar as the two ambiguous first-pass survivors: NGC2264 (also Sh2-273)
is confirmed the same physical object as "Fox Fur Nebula" (SIMBAD groups them under
one OID); "Sh2-105" for Crescent Nebula was *not* added despite coming from the
Crescent Nebula's own Wikidata item -- SIMBAD lists 46 identifiers for NGC6888 and
"Sh2-105" isn't among them, and Sh2-105's own SIMBAD entry is a separate, independent
object at a measurably different position. A real cross-reference on one authoritative
source doesn't always hold up against another; this table only accepts what SIMBAD
independently corroborates when there's any doubt.

Keyed by the exact catalog_name string each provider already produces (e.g. "NGC6888",
"Sh2-101", "RCW 146", "M1", "B33", "Arp 317", "HCG 92", "IC1805") -- no further
normalization needed at lookup time. See CompositeProvider's own use of this for how
it's applied: only ever backfills Annotation.common_name when a provider didn't already
set one, exactly the same non-clobbering precedent as every other cross-provider
enrichment field in this module.
"""

from __future__ import annotations

COMMON_NAMES: dict[str, str] = {
    "Arp 317": "Leo Triplet",
    "B150": "Seahorse Nebula",
    "B33": "Horsehead Nebula",
    "B59": "Dark Horse",
    "B72": "Snake Nebula",
    "B85": "Trifid Nebula",
    "HCG 57": "Copeland Septet",
    "HCG 79": "Seyfert's Sextet",
    "HCG 92": "Stephan's Quintet",
    "IC1318": "Sadr Region",
    "IC1805": "Heart Nebula",
    "IC1848": "Soul Nebula",
    "IC2220": "Toby Jug Nebula",
    "IC410": "Tadpole Nebula",
    "IC418": "Spirograph Nebula",
    "IC4628": "Prawn Nebula",
    "IC4703": "Eagle Nebula",
    "IC4715": "Sagittarius Star Cloud",
    "IC4895": "Barnard's Galaxy",
    "IC5070": "Pelican Nebula",
    "M1": "Crab Nebula",
    "M101": "Pinwheel Galaxy",
    "M104": "Sombrero Galaxy",
    "M11": "Wild Duck Cluster",
    "M16": "Eagle Nebula",
    "M17": "Omega Nebula",
    "M20": "Trifid Nebula",
    "M24": "Sagittarius Star Cloud",
    "M27": "Dumbbell Nebula",
    "M31": "Andromeda Galaxy",
    "M33": "Triangulum Galaxy",
    "M42": "Orion Nebula",
    "M44": "Beehive Cluster",
    "M45": "Pleiades",
    "M51": "Whirlpool Galaxy",
    "M57": "Ring Nebula",
    "M6": "Butterfly Cluster",
    "M63": "Sunflower Galaxy",
    "M64": "Black Eye Galaxy",
    "M76": "Little Dumbbell Nebula",
    "M8": "Lagoon Nebula",
    "M97": "Owl Nebula",
    "NGC1499": "California Nebula",
    "NGC1952": "Crab Nebula",
    "NGC1976": "Orion Nebula",
    "NGC2024": "Flame Nebula",
    "NGC224": "Andromeda Galaxy",
    "NGC2264": "Fox Fur Nebula",
    "NGC2419": "Intergalactic Wanderer",
    "NGC253": "Sculptor Galaxy",
    "NGC2632": "Beehive Cluster",
    "NGC292": "Small Magellanic Cloud",
    "NGC3372": "Carina Nebula",
    "NGC3587": "Owl Nebula",
    "NGC4567": "Siamese Twins",
    "NGC4594": "Sombrero Galaxy",
    "NGC4676": "Mice Galaxies",
    "NGC4755": "Jewel Box",
    "NGC4826": "Black Eye Galaxy",
    "NGC5055": "Sunflower Galaxy",
    "NGC5128": "Centaurus A",
    "NGC5139": "Omega Centauri",
    "NGC5194": "Whirlpool Galaxy",
    "NGC5457": "Pinwheel Galaxy",
    "NGC598": "Triangulum Galaxy",
    "NGC6369": "Little Ghost Nebula",
    "NGC6405": "Butterfly Cluster",
    "NGC650": "Little Dumbbell Nebula",
    "NGC651": "Little Dumbbell Nebula",
    "NGC6514": "Trifid Nebula",
    "NGC6523": "Lagoon Nebula",
    "NGC6537": "Red Spider Nebula",
    "NGC6543": "Cat's Eye Nebula",
    "NGC6611": "Eagle Nebula",
    "NGC6618": "Omega Nebula",
    "NGC6705": "Wild Duck Cluster",
    "NGC6720": "Ring Nebula",
    "NGC6822": "Barnard's Galaxy",
    "NGC6853": "Dumbbell Nebula",
    "NGC6888": "Crescent Nebula",
    "NGC6960": "The Western Veil",
    "NGC6992": "The Eastern Veil",
    "NGC7000": "North America Nebula",
    "NGC7009": "Saturn Nebula",
    "NGC7023": "Iris Nebula",
    "NGC7293": "Helix Nebula",
    "RCW 146": "Lagoon Nebula",
    "RCW 53": "Carina Nebula",
    "RCW 77": "Hourglass Nebula",
    "Sh2-101": "Tulip Nebula",
    "Sh2-103": "Cygnus Loop",
    "Sh2-106": "Celestial Snow Angel",
    "Sh2-117": "North America Nebula",
    "Sh2-119": "Clamshell Nebula",
    "Sh2-136": "Ghost Nebula",
    "Sh2-155": "Cave Nebula",
    "Sh2-190": "Heart Nebula",
    "Sh2-199": "Soul Nebula",
    "Sh2-220": "California Nebula",
    "Sh2-244": "Crab Nebula",
    "Sh2-25": "Lagoon Nebula",
    "Sh2-261": "Lower's Nebula",
    "Sh2-273": "Fox Fur Nebula",
    "Sh2-274": "Medusa Nebula",
    "Sh2-275": "Rosette Nebula",
    "Sh2-276": "Barnard's Loop",
    "Sh2-281": "Orion Nebula",
    "Sh2-45": "Omega Nebula",
}
