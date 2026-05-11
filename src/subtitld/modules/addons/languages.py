"""BCP-47 language tag handling for the add-on filter system.

Manifests (and the catalog rows the AddonsPanel renders) declare the
languages they support as a flat list of lowercased BCP-47 tags:

    "languages": ["en-us", "en-gb", "pt-br", "pt-pt", "de", "es-es", ...]

The UI lets the user filter by a single language. The match is intentionally
asymmetric and follows RFC 4647 "extended filtering" semantics, because the
tags users care about have a hierarchy:

    user picks   addon declares   match?  why
    -----------  ---------------  ------  ----
    pt-br        pt-br            YES     exact
    pt-br        pt               YES     addon supports generic Portuguese,
                                          which includes Brazilian
    pt-br        pt-pt            NO      different variant — listeners can
                                          tell, won't accept European voice
    pt           pt-br            YES     user wants any Portuguese, addon
                                          supports a variant
    pt           es               NO      different language

In other words: tags match if one is a prefix of the other in the
dash-separated subtag sense. They do NOT match just because they share a
common ancestor — which is what protects pt-br vs pt-pt from being
confused.

Outside the matching helper this module also normalizes raw inputs (Piper
uses `pt_BR`, Edge TTS uses `pt-BR-Name`, Vosk uses `pt`). Callers run
input through `normalize` before storing into a manifest or matching.
"""

from __future__ import annotations

import re

# Allow letters and digits in subtags (e.g. zh-cn, en-us, sl-IT, but also
# the rare numeric region codes like es-419 for Latin American Spanish).
_TAG_RE = re.compile(r'^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$')


def normalize(tag: str) -> str:
    """Coerce a free-form language tag to the canonical lowercased BCP-47
    form we store in manifests.

    Handles the common variations across upstream sources:
      - Piper / `voices.json` uses underscore + uppercase region (`pt_BR`)
      - Edge TTS uses dash + uppercase region with a name suffix (`pt-BR-Some`)
      - Vosk uses just primary (`pt`)
      - Catalog files we control should already be lowercase

    Empty / malformed input returns ''.
    """
    if not tag:
        return ''
    s = str(tag).strip().replace('_', '-').lower()
    # Drop trailing junk like Edge TTS's voice-name suffix on "pt-br-some".
    # We keep at most language + region (the two leading subtags) — that's
    # the granularity the filter UI works with. Variant subtags get folded
    # into the region for matching purposes.
    if not s:
        return ''
    parts = s.split('-')
    # Keep the first two non-empty subtags. `pt-BR-FrancoNeural` → `pt-br`.
    keep = [p for p in parts if p][:2]
    out = '-'.join(keep)
    if not _TAG_RE.match(out):
        # Garbage in, empty out — caller decides whether to drop the tag.
        return ''
    return out


def primary_subtag(tag: str) -> str:
    """Return the language part only (`pt-br` → `pt`). Used for grouping in
    UI dropdowns when we want a "Portuguese (any)" rollup."""
    norm = normalize(tag)
    if not norm:
        return ''
    return norm.split('-', 1)[0]


def tag_matches(filter_tag: str, addon_tag: str) -> bool:
    """RFC 4647 "extended filtering": match when one tag is a strict prefix
    of the other in the dash-separated subtag sense.

    Both inputs are normalized first, so this is safe to call with raw
    upstream strings.

    Examples (the cases we unit-test):
        tag_matches('pt-br', 'pt-br') is True          # exact
        tag_matches('pt-br', 'pt')    is True          # filter narrower
        tag_matches('pt-br', 'pt-pt') is False         # different region
        tag_matches('pt',    'pt-br') is True          # filter broader
        tag_matches('pt',    'es')    is False
        tag_matches('',      'pt')    is False         # empty filter never matches
    """
    f = normalize(filter_tag)
    a = normalize(addon_tag)
    if not f or not a:
        return False
    if f == a:
        return True
    if a.startswith(f + '-'):
        return True
    if f.startswith(a + '-'):
        return True
    return False


def any_match(filter_tag: str, addon_tags: list[str]) -> bool:
    """True iff any tag in `addon_tags` matches `filter_tag`. Convenience
    wrapper for the filter-row code path, which compares ONE user pick
    against a list of addon-declared tags."""
    if not filter_tag:
        return True  # "Any language" filter — pass everything through.
    if not addon_tags:
        return False  # Addon declared no languages — can't satisfy a filter.
    return any(tag_matches(filter_tag, t) for t in addon_tags)


def collect_unique_tags(rows_languages: list[list[str]]) -> list[str]:
    """Union of all tags across many addons, sorted, normalized, deduped.
    Used to populate the filter dropdown — caller passes a list-of-lists,
    one inner list per addon row, and gets back a single flat sorted list.
    """
    seen: set[str] = set()
    for row_tags in rows_languages:
        if not row_tags:
            continue
        for tag in row_tags:
            n = normalize(tag)
            if n:
                seen.add(n)
    return sorted(seen)


# ---------------------------------------------------------------------------
# Display names
# ---------------------------------------------------------------------------
# Static English names for the languages our reference add-ons declare. We
# deliberately don't lean on Qt's QLocale here because:
#   - QLocale's strings are localized to the running app, which is
#     inconsistent with our other UI strings (currently English-only).
#   - Some of our tags (e.g. `zh-cn`) parse oddly through QLocale on
#     older Qt versions and yield "Chinese (Mainland China)" or worse.
#   - Keeping it data-only lets this module stay free of UI dependencies.
#
# Tags not in this map fall back to the upper-cased subtag, so the filter
# is still navigable for an unlisted language — just with the raw code.

_LANGUAGE_NAMES: dict[str, str] = {
    # The list covers every primary subtag advertised by the reference
    # add-ons (Vosk, Piper, Coqui XTTS) plus a handful of common ones the
    # user might encounter via third-party add-ons. Order alphabetic for
    # easy maintenance — new entries fit obviously.
    'ar': 'Arabic',
    'bg': 'Bulgarian',
    'ca': 'Catalan',
    'cs': 'Czech',
    'cy': 'Welsh',
    'da': 'Danish',
    'de': 'German',
    'el': 'Greek',
    'en': 'English',
    'es': 'Spanish',
    'eu': 'Basque',
    'fa': 'Persian',
    'fi': 'Finnish',
    'fr': 'French',
    'he': 'Hebrew',
    'hi': 'Hindi',
    'hu': 'Hungarian',
    'id': 'Indonesian',
    'is': 'Icelandic',
    'it': 'Italian',
    'ja': 'Japanese',
    'ka': 'Georgian',
    'kk': 'Kazakh',
    'ko': 'Korean',
    'ku': 'Kurdish',
    'lb': 'Luxembourgish',
    'lv': 'Latvian',
    'ml': 'Malayalam',
    'ne': 'Nepali',
    'nl': 'Dutch',
    'no': 'Norwegian',
    'pl': 'Polish',
    'pt': 'Portuguese',
    'ro': 'Romanian',
    'ru': 'Russian',
    'sk': 'Slovak',
    'sl': 'Slovenian',
    'sq': 'Albanian',
    'sr': 'Serbian',
    'sv': 'Swedish',
    'sw': 'Swahili',
    'te': 'Telugu',
    'th': 'Thai',
    'tr': 'Turkish',
    'uk': 'Ukrainian',
    'ur': 'Urdu',
    'vi': 'Vietnamese',
    'zh': 'Chinese',
}

_REGION_NAMES: dict[str, str] = {
    # ISO 3166-1 alpha-2 codes mapped to English names. Same alphabetic
    # ordering as the language map. Add entries as new add-on locales
    # appear; the fallback is the upper-cased tag (e.g. unknown 'ag'
    # renders as 'AG'), which is acceptable but ugly — fix here when
    # spotted.
    'al': 'Albania',
    'ar': 'Argentina',
    'be': 'Belgium',
    'bg': 'Bulgaria',
    'br': 'Brazil',
    'cd': 'DR Congo',
    'cn': 'China',
    'cz': 'Czech Republic',
    'de': 'Germany',
    'dk': 'Denmark',
    'es': 'Spain',
    'fi': 'Finland',
    'fr': 'France',
    'gb': 'United Kingdom',
    'ge': 'Georgia',
    'gr': 'Greece',
    'in': 'India',
    'id': 'Indonesia',
    'ir': 'Iran',
    'is': 'Iceland',
    'it': 'Italy',
    'jo': 'Jordan',
    'jp': 'Japan',
    'kr': 'South Korea',
    'kz': 'Kazakhstan',
    'lu': 'Luxembourg',
    'lv': 'Latvia',
    'mx': 'Mexico',
    'nl': 'Netherlands',
    'no': 'Norway',
    'np': 'Nepal',
    'pk': 'Pakistan',
    'pl': 'Poland',
    'pt': 'Portugal',
    'ro': 'Romania',
    'rs': 'Serbia',
    'ru': 'Russia',
    'se': 'Sweden',
    'si': 'Slovenia',
    'sk': 'Slovakia',
    'tr': 'Turkey',
    'tw': 'Taiwan',
    'ua': 'Ukraine',
    'us': 'United States',
    'vn': 'Vietnam',
}


def display_name(tag: str) -> str:
    """Render a normalized BCP-47 tag as a human-readable English name.

        display_name('en-us') -> 'English (United States)'
        display_name('pt')    -> 'Portuguese'
        display_name('zh-cn') -> 'Chinese (China)'
        display_name('xx-yy') -> 'XX (YY)'   (graceful fallback)
        display_name('')      -> ''

    The fallback shape upper-cases unknown subtags so a brand-new language
    in the catalog still reads as a recognisable filter entry rather than
    blowing up the dropdown.
    """
    norm = normalize(tag)
    if not norm:
        return ''
    parts = norm.split('-')
    primary = parts[0]
    base = _LANGUAGE_NAMES.get(primary, primary.upper())
    if len(parts) == 1:
        return base
    region = parts[1]
    region_name = _REGION_NAMES.get(region, region.upper())
    return f'{base} ({region_name})'
