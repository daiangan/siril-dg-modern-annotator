"""Gum (1955, HII regions in the southern Milky Way) -- sourced the same way as
sh2_corrected_positions.py: Kevin Jardine's "Integrated HII Regions" catalog
(galaxymap.org, Creative Commons licensed), retrieved 2026-09-02, this time using its
Gum-tagged rows rather than its Sharpless corrections.

Per GitHub issue #10 (github.com/daiangan/siril-dg-modern-annotator/issues/10) and
discussed live: not on VizieR at all (like Sh2's correction), so this is a static,
versioned snapshot bundled as plain Python data rather than fetched at runtime.

Confirmed live before implementing: 47 of these 67 objects already carry an RCW
cross-reference in Jardine's own data (i.e. the same physical nebula this app can
already show as an RCW marker) -- "gum" is added to catalogs._DEEP_SKY_CATALOGS so
those merge into one marker with RCW/NGC/Sh2/etc. instead of drawing twice, the same
treatment RCW itself got. The genuinely new content is the remaining ~20 objects,
including the Gum Nebula itself ("Gum nebula", by far the largest entry here).

Known limitation, confirmed live on SIMBAD: roughly a third of these names carry a
letter suffix (e.g. "Gum 74b", a sub-component of a larger complex) and at least one
suffixed name ("Gum 74b") resolves on SIMBAD to an unrelated star rather than the
nebula -- a naming collision in SIMBAD itself, not something fixable from this data.
Plain-numbered names ("Gum 15", etc.) were confirmed live to resolve correctly.
"Open in SIMBAD" may be unreliable for suffixed Gum objects; not addressed here.

name -> (ra_deg, dec_deg, angular_size_arcmin_or_None), J2000.
"""

from __future__ import annotations

GUM_OBJECTS: dict[str, tuple[float, float, float | None]] = {
    'Gum 74b': (271.180792, -23.545944, 19.4548),
    'Gum 75': (272.325792, -24.010861, 15.3502),
    'Gum 81a': (275.317583, -16.222056, 15.0),
    'Gum 81b': (274.964708, -15.942111, 12.5),
    'Gum 85': (274.471417, -11.730028, 2.0),
    'Gum 10': (124.050875, -35.574639, 12.5),
    'Gum 11': (125.197625, -36.217194, 1.0),
    'Gum 12a': (119.57525, -43.271361, 600.0),
    'Gum 13': (125.460083, -42.602556, 4.7515),
    'Gum 14': (129.705542, -40.368944, 52.7234),
    'Gum nebula': (127.663333, -41.747306, 1166.0495),
    'Gum 15': (131.240583, -41.282917, 14.0926),
    'Gum 16': (128.106417, -44.090361, 120.0),
    'Gum 17': (132.836667, -42.175111, 50.0),
    'Gum 19': (134.106333, -43.101056, 1.5),
    'Gum 18': (132.861667, -43.953083, 20.0),
    'Gum 20': (134.856167, -43.741306, 6.0628),
    'Gum 21': (133.569292, -47.547111, 25.0),
    'Gum 22': (134.762792, -47.472917, 2.0),
    'Gum 23': (134.970042, -47.436694, 13.0848),
    'Gum 12b': (147.290417, -38.465806, 210.0),
    'Gum 24': (135.917708, -48.390333, 6.0525),
    'Gum 25': (135.578167, -48.681028, 7.4403),
    'Gum 26': (141.106167, -51.983389, 7.9616),
    'Gum 27': (141.691375, -56.134639, 1.0),
    'Gum 28': (154.191708, -57.907278, 11.3494),
    'Gum 29': (156.159917, -57.725861, 18.2457),
    'Gum 30': (158.498292, -58.130333, 23.9059),
    'Gum 31': (159.430875, -58.653056, 10.0),
    'Gum 32': (161.562417, -58.643417, 3.5),
    'Gum 33': (160.963792, -59.858194, 90.0),
    'Gum 34a': (164.521125, -59.701972, 7.5),
    'Gum 34b': (165.8565, -59.435917, 29.523),
    'Gum 35': (164.605875, -61.252222, 5.0),
    'Gum 36': (168.0085, -58.793583, 5.0),
    'Gum 37': (167.489583, -60.109167, 10.0),
    'Gum 38a': (168.013667, -61.210278, 10.0),
    'Gum 38b': (168.776583, -61.211833, 6.0),
    'Gum 39': (172.226458, -62.667639, 10.0),
    'Gum 40': (172.250167, -62.934333, 6.0),
    'Gum 41': (172.600875, -63.818167, 7.5),
    'Gum 42': (174.591375, -63.353806, 37.5),
    'Gum 43': (188.748292, -61.618056, 0.5),
    'Gum 44': (188.927833, -61.851139, 0.5),
    'Gum 45': (191.0195, -62.48125, 2.0),
    'Gum 46': (192.615125, -61.578278, 1.5),
    'Gum 48a': (199.940583, -62.471389, 10.7858),
    'Gum 47': (203.3525, -65.964556, 1.5),
    'Gum 48b': (203.396125, -62.366194, 15.8718),
    'Gum 48c': (205.019917, -61.728444, 8.455),
    'Gum 48d': (206.831292, -62.618, 6.1869),
    'Gum 49': (238.912792, -54.642361, 7.2854),
    'Gum 50': (239.903125, -53.753972, 1.0),
    'Gum 51': (242.613833, -48.989583, 18.5112),
    'Gum 52': (248.452458, -48.112861, 3.5),
    'Gum 53': (250.123, -48.781194, 105.0),
    'Gum 54': (253.859083, -45.065167, 26.3244),
    'Gum 55': (253.0095, -42.170889, 120.0),
    'Gum 56': (254.2325, -40.326083, 45.0),
    'Gum 57a': (255.265833, -38.290306, 12.5),
    'Gum 59': (260.018708, -38.344083, 51.9702),
    'Gum 61': (259.953917, -36.105167, 3.5),
    'Gum 63': (259.521167, -35.735833, 14.2057),
    'Gum 62': (260.229042, -36.085861, 4.0),
    'Gum 64a': (260.027083, -35.971111, 1.0),
    'Gum 64b': (260.226125, -35.885889, 3.0),
    'Gum 64c': (260.048958, -35.754222, 6.0),
}
