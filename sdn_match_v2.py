#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sdn_match_v2.py

Compares Principals_Alpha records against OFAC SDN entries across name,
address, and geographic fields using Direct, Soundex, Double-Metaphone,
NYSIIS, and Jaro-Winkler methods.

For each input record x SDN candidate x field pair x method, generates
one output row at both full-text and word-by-word granularity.

Output tables (in --out-database)
----------------------------------
  MatchingInput_v2          One row per input record processed.
  MatchingResults_v2        One row per comparison (full-text and word-level).
  MatchingResults_v2_RunLog One row per run.

Phonetic algorithms are imported from sdn_match.py (must be in the same
directory).  Address abbreviation map (dbo.Address_Abbreviation) is loaded
from the SDN database.

Usage
-----
  python sdn_match_v2.py [options]

Options
-------
  --ca-server / --ca-database  California DB  (default: . / California)
  --sdn-server / --sdn-database  SDN DB       (default: . / SDN)
  --out-server / --out-database  Output DB    (default: . / SDNReporting)
  --out-schema                               (default: dbo)
  --entity-name   Filter Principals_Alpha by ENTITY_NAME prefix; * = all
  --drop-output   DROP and recreate output tables before run
  --no-csv        Skip CSV output
  --output        Detail CSV path           (default: MatchingResults_v2.csv)
  --config        Config file path          (default: sdn_match_v2.cfg)
  --max-addr-candidates  Max SDN address records to compare per input record
                         (default: 50)
"""

import argparse
import csv
import math
import multiprocessing
import random
import re
import sys
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Optional, List

try:
    import pyodbc
except ImportError:
    sys.exit("pyodbc not installed.  Run: pip install pyodbc")

try:
    from sdn_match import (
        _soundex, _dm_codes, _nysiis_codes,
        _jaro_winkler_similarity, _score,
        load_config, _DM_AVAILABLE,
    )
except ImportError as e:
    sys.exit(f"Cannot import from sdn_match.py: {e}\n"
             "Ensure sdn_match.py is in the same directory.")

# ---------------------------------------------------------------------------
# Fast JW — use rapidfuzz C++ implementation when available; fall back to
# the pure-Python _jaro_winkler_similarity imported from sdn_match.py.
# rapidfuzz.distance.JaroWinkler uses prefix_weight=0.1 by default,
# matching our implementation exactly.
# ---------------------------------------------------------------------------
try:
    from rapidfuzz.distance import JaroWinkler as _rfJW
    def _jaro_winkler_fast(s1: str, s2: str) -> float:
        return _rfJW.normalized_similarity(s1, s2)
    _RAPIDFUZZ = True
except ImportError:
    _jaro_winkler_fast = _jaro_winkler_similarity   # pure-Python fallback
    _RAPIDFUZZ = False

try:
    import duckdb as _duckdb
    _DUCKDB = True
except ImportError:
    _duckdb = None
    _DUCKDB = False


# Entity type expansion pairs are stored in EntityTypeMap (SDN database) — see load_entity_type_map()

STATE_ABBREV_MAP = {
    'AL': 'ALABAMA',              'AK': 'ALASKA',               'AZ': 'ARIZONA',
    'AR': 'ARKANSAS',             'CA': 'CALIFORNIA',            'CO': 'COLORADO',
    'CT': 'CONNECTICUT',          'DE': 'DELAWARE',              'FL': 'FLORIDA',
    'GA': 'GEORGIA',              'HI': 'HAWAII',                'ID': 'IDAHO',
    'IL': 'ILLINOIS',             'IN': 'INDIANA',               'IA': 'IOWA',
    'KS': 'KANSAS',               'KY': 'KENTUCKY',              'LA': 'LOUISIANA',
    'ME': 'MAINE',                'MD': 'MARYLAND',              'MA': 'MASSACHUSETTS',
    'MI': 'MICHIGAN',             'MN': 'MINNESOTA',             'MS': 'MISSISSIPPI',
    'MO': 'MISSOURI',             'MT': 'MONTANA',               'NE': 'NEBRASKA',
    'NV': 'NEVADA',               'NH': 'NEW HAMPSHIRE',         'NJ': 'NEW JERSEY',
    'NM': 'NEW MEXICO',           'NY': 'NEW YORK',              'NC': 'NORTH CAROLINA',
    'ND': 'NORTH DAKOTA',         'OH': 'OHIO',                  'OK': 'OKLAHOMA',
    'OR': 'OREGON',               'PA': 'PENNSYLVANIA',          'RI': 'RHODE ISLAND',
    'SC': 'SOUTH CAROLINA',       'SD': 'SOUTH DAKOTA',          'TN': 'TENNESSEE',
    'TX': 'TEXAS',                'UT': 'UTAH',                  'VT': 'VERMONT',
    'VA': 'VIRGINIA',             'WA': 'WASHINGTON',            'WV': 'WEST VIRGINIA',
    'WI': 'WISCONSIN',            'WY': 'WYOMING',               'DC': 'DISTRICT OF COLUMBIA',
    'PR': 'PUERTO RICO',          'GU': 'GUAM',                  'VI': 'VIRGIN ISLANDS',
    'AS': 'AMERICAN SAMOA',       'MP': 'NORTHERN MARIANA ISLANDS',
}

COUNTRY_ABBREV_MAP = {
    'US': 'UNITED STATES',        'USA': 'UNITED STATES',
    'GB': 'UNITED KINGDOM',       'UK':  'UNITED KINGDOM',
    'CA': 'CANADA',               'AU':  'AUSTRALIA',            'NZ': 'NEW ZEALAND',
    'DE': 'GERMANY',              'FR':  'FRANCE',               'IT': 'ITALY',
    'ES': 'SPAIN',                'PT':  'PORTUGAL',             'NL': 'NETHERLANDS',
    'BE': 'BELGIUM',              'CH':  'SWITZERLAND',          'AT': 'AUSTRIA',
    'SE': 'SWEDEN',               'NO':  'NORWAY',               'DK': 'DENMARK',
    'FI': 'FINLAND',              'IE':  'IRELAND',              'PL': 'POLAND',
    'CZ': 'CZECH REPUBLIC',       'RU':  'RUSSIA',               'CN': 'CHINA',
    'JP': 'JAPAN',                'KR':  'SOUTH KOREA',          'IN': 'INDIA',
    'MX': 'MEXICO',               'BR':  'BRAZIL',               'AR': 'ARGENTINA',
    'SA': 'SAUDI ARABIA',         'AE':  'UNITED ARAB EMIRATES', 'IL': 'ISRAEL',
    'TR': 'TURKEY',               'EG':  'EGYPT',                'ZA': 'SOUTH AFRICA',
    'NG': 'NIGERIA',              'KE':  'KENYA',                'IR': 'IRAN',
    'IQ': 'IRAQ',                 'SY':  'SYRIA',                'LB': 'LEBANON',
    'JO': 'JORDAN',               'KW':  'KUWAIT',               'QA': 'QATAR',
    'BH': 'BAHRAIN',              'OM':  'OMAN',                 'YE': 'YEMEN',
    'PK': 'PAKISTAN',             'AF':  'AFGHANISTAN',          'UA': 'UKRAINE',
    'BY': 'BELARUS',              'KZ':  'KAZAKHSTAN',           'AZ': 'AZERBAIJAN',
    'GE': 'GEORGIA',              'AM':  'ARMENIA',              'UZ': 'UZBEKISTAN',
    'CU': 'CUBA',                 'VE':  'VENEZUELA',            'CO': 'COLOMBIA',
    'PE': 'PERU',                 'CL':  'CHILE',                'EC': 'ECUADOR',
    'BO': 'BOLIVIA',              'PY':  'PARAGUAY',             'UY': 'URUGUAY',
    'PA': 'PANAMA',               'CR':  'COSTA RICA',           'GT': 'GUATEMALA',
    'HN': 'HONDURAS',             'NI':  'NICARAGUA',            'SV': 'EL SALVADOR',
    'DO': 'DOMINICAN REPUBLIC',   'HT':  'HAITI',
    'LY': 'LIBYA',                'MA':  'MOROCCO',              'DZ': 'ALGERIA',
    'TN': 'TUNISIA',              'SD':  'SUDAN',                'SO': 'SOMALIA',
    'ET': 'ETHIOPIA',             'CD':  'DEMOCRATIC REPUBLIC OF THE CONGO',
    'SG': 'SINGAPORE',            'MY':  'MALAYSIA',             'ID': 'INDONESIA',
    'TH': 'THAILAND',             'VN':  'VIETNAM',              'PH': 'PHILIPPINES',
    'MM': 'MYANMAR',              'BD':  'BANGLADESH',           'LK': 'SRI LANKA',
    'NP': 'NEPAL',                'HK':  'HONG KONG',            'TW': 'TAIWAN',
}

_WHITESPACE       = re.compile(r'\s+')
_PUNCT_TO_SPACE   = re.compile(r'[.,;\-/\\]+')

# Phonetic name normalisation — PH → F so that "PHree" and "Free" compare
# as identical strings before Jaro-Winkler is applied.  Applied to already-
# uppercased normalized name tokens only (never to addresses or raw values).
_PH_RE = re.compile(r'PH')

def _ph_norm_name(s: str) -> str:
    """Replace PH with F in an already-uppercased normalized name string."""
    return _PH_RE.sub('F', s) if s else s

# Remarks parsing — Linked To and Phone
_LINKED_TO_PAT   = re.compile(r'Linked\s+to:\s*(.+?)(?=\s*[;.]|\s*Linked\s+to:|$)',
                               re.IGNORECASE)
_PHONE_FIELD_PAT = re.compile(
    r'(?:Tel(?:ephone)?|Phone|Fax)[.\s:]*([+\d][\d\s\-\(\)\.\/+]{5,})',
    re.IGNORECASE
)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_v2_config(path: str) -> dict:
    """
    Reads sdn_match_v2.cfg.  Falls back to sdn_match.cfg for [MatchTypeScores].
    Returns dict with keys:
      scores         {matchtype: int}
      keep_chars     str (e.g. '-#')
      min_jw_addr    float (min JW similarity to report an address match)
    """
    import configparser
    cfg = configparser.ConfigParser()
    cfg.read([path, 'sdn_match.cfg'])

    if 'MatchTypeScores' not in cfg:
        sys.exit(f"[MatchTypeScores] section not found in {path} or sdn_match.cfg")

    scores = {}
    label_map = {
        'direct': 'Direct', 'soundex': 'Soundex',
        'jaro-winkler': 'Jaro-Winkler',
        'double-metaphone': 'Double-Metaphone', 'nysiis': 'NYSIIS',
    }
    for key, val in cfg['MatchTypeScores'].items():
        matched = label_map.get(key.strip().lower(), key.strip())
        try:
            scores[matched] = int(val)
        except ValueError:
            sys.exit(f"Non-integer score for '{key}': {val!r}")

    norm = cfg['Normalization'] if 'Normalization' in cfg else {}
    keep_chars = norm.get('keep_chars', '-#')

    addr = cfg['AddressMatch'] if 'AddressMatch' in cfg else {}
    try:
        min_jw_addr = float(addr.get('min_jw_similarity', '0.70'))
    except ValueError:
        min_jw_addr = 0.70

    matching = cfg['Matching'] if 'Matching' in cfg else {}
    use_phonetic = matching.get('use_phonetic', 'false').strip().lower() == 'true'

    indiv = cfg['IndividualMatch'] if 'IndividualMatch' in cfg else {}
    try:
        jw_name_threshold = float(indiv.get('JaroWinklerMatchThreshold', '0.75'))
    except ValueError:
        jw_name_threshold = 0.75

    org = cfg['OrgNameMatch'] if 'OrgNameMatch' in cfg else {}
    try:
        jw_org_threshold = float(org.get('JaroWinklerMatchThreshold', '0.75'))
    except ValueError:
        jw_org_threshold = 0.75
    try:
        jw_org_aka_threshold = float(org.get('JaroWinklerMatchThreshold_AKA', '0.75'))
    except ValueError:
        jw_org_aka_threshold = 0.75

    return {'scores': scores, 'keep_chars': keep_chars,
            'min_jw_addr': min_jw_addr, 'use_phonetic': use_phonetic,
            'jw_name_threshold': jw_name_threshold,
            'jw_org_threshold': jw_org_threshold,
            'jw_org_aka_threshold': jw_org_aka_threshold}


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def _build_strip_pattern(keep_chars: str) -> re.Pattern:
    """Regex that strips everything except alphanumeric, spaces, and keep_chars."""
    escaped = re.escape(keep_chars)
    return re.compile(rf'[^A-Za-z0-9\s{escaped}]')


def normalize(s: str, strip_pat: re.Pattern) -> str:
    """Remove disallowed punctuation, collapse whitespace, uppercase."""
    if not s:
        return ''
    s = strip_pat.sub('', s).strip()
    return _WHITESPACE.sub(' ', s).upper()


def expand_address_nm(raw: str, abbrev_map: dict, strip_pat: re.Pattern) -> str:
    """USPS abbreviation expansion then normalize."""
    if not raw:
        return ''
    s = _PUNCT_TO_SPACE.sub(' ', raw)
    tokens = _WHITESPACE.split(s.strip())
    first_alpha = next(
        (i for i, t in enumerate(tokens) if t and re.search(r'[A-Za-z]', t)), None
    )
    out = []
    for i, tok in enumerate(tokens):
        if not tok:
            continue
        mappings = abbrev_map.get(tok.upper())
        if mappings:
            is_first = (i == first_alpha)
            if len(mappings) == 1:
                out.append(mappings[0][0])
            elif is_first:
                out.append(mappings[0][0])
            else:
                out.append(next((f for f, sk in mappings if sk == 1), mappings[0][0]))
        else:
            out.append(tok)
    return normalize(' '.join(out), strip_pat)


def expand_entity_nm(raw: str, strip_pat: re.Pattern,
                     entity_map: dict = None) -> str:
    """Expand entity type abbreviations (prefix and/or suffix) then normalize.

    When entity_map is provided (loaded via load_entity_type_map), both the
    first token (prefix) and last token (suffix) are checked for expansion.
    For single-token names the suffix check takes priority.
    When entity_map is None the function is a no-op expander (normalize only).
    """
    if not raw:
        return ''
    tokens = [t for t in _WHITESPACE.split(raw.strip()) if t]
    if not tokens:
        return ''
    if entity_map is not None:
        suffix_abbr = entity_map.get('suffix_abbr', {})
        prefix_abbr = entity_map.get('prefix_abbr', {})
        # Check suffix (last token)
        last_clean = re.sub(r'\.', '', tokens[-1]).upper()
        if last_clean in suffix_abbr:
            tokens[-1] = suffix_abbr[last_clean]
            # Only check prefix if name has more than one token
            if len(tokens) > 1:
                first_clean = re.sub(r'\.', '', tokens[0]).upper()
                if first_clean in prefix_abbr:
                    tokens[0] = prefix_abbr[first_clean]
        else:
            # No suffix match — check prefix
            first_clean = re.sub(r'\.', '', tokens[0]).upper()
            if first_clean in prefix_abbr:
                tokens[0] = prefix_abbr[first_clean]
    return normalize(' '.join(tokens), strip_pat)


def normalize_state(s: str, strip_pat: re.Pattern) -> str:
    """Normalize state/province string; expand 2-char US state abbreviations."""
    if not s:
        return ''
    cleaned = normalize(s, strip_pat)
    return STATE_ABBREV_MAP.get(cleaned.strip(), cleaned)


def normalize_country(s: str, strip_pat: re.Pattern) -> str:
    """Normalize country string; expand ISO 3166-1 alpha-2/3 codes."""
    if not s:
        return ''
    cleaned = normalize(s, strip_pat)
    return COUNTRY_ABBREV_MAP.get(cleaned.strip(), cleaned)


def load_abbrev_map(conn) -> dict:
    rows = conn.cursor().execute(
        "SELECT UPPER(LTRIM(RTRIM(Address_Part_Abbreviation))), "
        "       LTRIM(RTRIM(Address_Part)), "
        "       ISNULL(Skip_At_Beginning, 0) "
        "FROM   dbo.Address_Abbreviation "
        "WHERE  Address_Part_Abbreviation IS NOT NULL "
        "  AND  Address_Part IS NOT NULL"
    ).fetchall()
    am = defaultdict(list)
    for abbrev, full, skip in rows:
        am[abbrev].append((full, int(skip)))
    for k in am:
        am[k].sort(key=lambda x: x[1])
    am['#'] = [('Number', 0)]
    return dict(am)


def load_entity_type_map(conn) -> dict:
    """Load EntityTypeMap from the SDN database.

    Returns a dict with keys:
      'suffix_abbr'   : {ABBR -> EXPANDED} for rows where Position in (SUFFIX, BOTH)
      'prefix_abbr'   : {ABBR -> EXPANDED} for rows where Position in (PREFIX, BOTH)
      'suffix_phrases': sorted list of expanded forms (and abbrs) for suffix detection,
                        longest-phrase-first
      'prefix_phrases': sorted list of expanded forms (and abbrs) for prefix detection,
                        longest-phrase-first
      'canon'         : {ABBR -> EXPANDED, EXPANDED -> EXPANDED} for canonicalization
    """
    rows = conn.cursor().execute(
        "SELECT UPPER(LTRIM(RTRIM(ISNULL(Abbreviation,'')))), "
        "       UPPER(LTRIM(RTRIM(ExpandedForm))), "
        "       Position "
        "FROM   dbo.EntityTypeMap"
    ).fetchall()

    suffix_abbr:   dict = {}
    prefix_abbr:   dict = {}
    suffix_phrases_set: set = set()
    prefix_phrases_set: set = set()
    canon:         dict = {}

    for abbr, expanded, position in rows:
        pos = (position or '').upper().strip()
        if abbr:
            canon[abbr] = expanded
        canon[expanded] = expanded

        is_suffix = pos in ('SUFFIX', 'BOTH')
        is_prefix = pos in ('PREFIX', 'BOTH')

        if abbr and is_suffix:
            suffix_abbr[abbr] = expanded
        if abbr and is_prefix:
            prefix_abbr[abbr] = expanded

        if is_suffix:
            suffix_phrases_set.add(expanded)
            if abbr:
                suffix_phrases_set.add(abbr)
        if is_prefix:
            prefix_phrases_set.add(expanded)
            if abbr:
                prefix_phrases_set.add(abbr)

    suffix_phrases = sorted(suffix_phrases_set, key=lambda p: -len(p.split()))
    prefix_phrases = sorted(prefix_phrases_set, key=lambda p: -len(p.split()))

    return {
        'suffix_abbr':   suffix_abbr,
        'prefix_abbr':   prefix_abbr,
        'suffix_phrases': suffix_phrases,
        'prefix_phrases': prefix_phrases,
        'canon':         canon,
    }


# ---------------------------------------------------------------------------
# Input record
# ---------------------------------------------------------------------------

@dataclass
class InputRecord:
    # Raw
    entity_type:  Optional[str] = None   # Individual | Entity | Unknown
    entity_name:  Optional[str] = None
    external_id:  Optional[str] = None   # Source-system identifier (e.g. ENTITY_NUM)
    first_name:   Optional[str] = None
    middle_name:  Optional[str] = None
    last_name:    Optional[str] = None
    address1:     Optional[str] = None
    address2:     Optional[str] = None
    address3:     Optional[str] = None
    city:         Optional[str] = None
    region:       Optional[str] = None
    postal_code:  Optional[str] = None
    country:      Optional[str] = None
    phone:        Optional[str] = None   # raw phone number
    # Normalized
    entity_name_nm:  str = ''
    first_name_nm:   str = ''
    middle_name_nm:  str = ''
    last_name_nm:    str = ''
    address1_nm:     str = ''
    address2_nm:     str = ''
    address3_nm:     str = ''
    city_nm:         str = ''
    region_nm:       str = ''
    postal_code_nm:  str = ''
    country_nm:      str = ''
    phone_nm:        str = ''   # digits only


def _s(v) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip().rstrip('\r\n')
    return s or None


def normalize_input(rec: InputRecord, abbrev_map: dict,
                    strip_pat: re.Pattern,
                    entity_map: dict = None) -> InputRecord:
    rec.entity_name_nm  = _ph_norm_name(expand_entity_nm(_s(rec.entity_name) or '', strip_pat, entity_map))
    rec.first_name_nm   = _ph_norm_name(normalize(_s(rec.first_name)   or '', strip_pat))
    rec.middle_name_nm  = _ph_norm_name(normalize(_s(rec.middle_name)  or '', strip_pat))
    rec.last_name_nm    = _ph_norm_name(normalize(_s(rec.last_name)    or '', strip_pat))
    rec.address1_nm     = expand_address_nm(_s(rec.address1) or '', abbrev_map, strip_pat)
    rec.address2_nm     = expand_address_nm(_s(rec.address2) or '', abbrev_map, strip_pat)
    rec.address3_nm     = expand_address_nm(_s(rec.address3) or '', abbrev_map, strip_pat)
    rec.city_nm         = expand_address_nm(_s(rec.city)     or '', abbrev_map, strip_pat)
    rec.region_nm       = normalize_state(_s(rec.region)   or '', strip_pat)
    rec.postal_code_nm  = normalize(_s(rec.postal_code)   or '', strip_pat)
    rec.country_nm      = normalize_country(_s(rec.country) or '', strip_pat)
    rec.phone_nm        = re.sub(r'\D', '', _s(rec.phone) or '')
    return rec


# ---------------------------------------------------------------------------
# SDN loading
# ---------------------------------------------------------------------------

def load_sdn_names(conn, strip_pat: re.Pattern, sdn_limit: int = None) -> dict:
    """
    Load sdnEntry + akaList into phonetic/direct indexes.
    Identical index structure to sdn_match.py but includes akaList rows.
    """
    if sdn_limit:
        print(f"Loading SDN name entries (sdnEntry + akaList) — LIMIT {sdn_limit:,} SDN entries...")
        _uid_subq = f"SELECT TOP({sdn_limit}) uid FROM dbo.sdnEntry ORDER BY uid"
        sdn_rows = conn.cursor().execute(
            f"SELECT uid, firstName, lastName, sdnType FROM dbo.sdnEntry"
            f" WHERE uid IN ({_uid_subq})"
        ).fetchall()
        aka_rows = conn.cursor().execute(f"""
            SELECT e.uid, a.uid, a.firstName, a.lastName, a.category, e.sdnType
            FROM   dbo.akaList a
            JOIN   dbo.sdnEntry_akaList ea ON ea.akaList_uid   = a.uid
            JOIN   dbo.sdnEntry e          ON e.uid            = ea.sdnEntry_uid
            WHERE  e.uid IN ({_uid_subq})
        """).fetchall()
    else:
        print("Loading SDN name entries (sdnEntry + akaList)...")
        sdn_rows = conn.cursor().execute(
            "SELECT uid, firstName, lastName, sdnType FROM dbo.sdnEntry"
        ).fetchall()
        aka_rows = conn.cursor().execute("""
            SELECT e.uid, a.uid, a.firstName, a.lastName, a.category, e.sdnType
            FROM   dbo.akaList a
            JOIN   dbo.sdnEntry_akaList ea ON ea.akaList_uid   = a.uid
            JOIN   dbo.sdnEntry e          ON e.uid            = ea.sdnEntry_uid
        """).fetchall()

    by_ln = defaultdict(list);    by_fn = defaultdict(list)
    sdx_by_ln = defaultdict(list); sdx_by_fn = defaultdict(list)
    dm_by_ln  = defaultdict(list); dm_by_fn  = defaultdict(list)
    ny_by_ln  = defaultdict(list); ny_by_fn  = defaultdict(list)
    uid_to_ln = {};  uid_to_fn = {}
    uid_to_ln_orig = {};  uid_to_fn_orig = {}
    uid_to_ln_nm = {};   uid_to_fn_nm = {}   # normalized (strip_pat applied) — used for scoring
    uid_to_sdntype = {}
    aka_by_sdn        = defaultdict(list)   # sdnEntry_uid -> [(aka_uid, fn, ln, category)]
    entity_aka_by_sdn = defaultdict(list)   # sdnEntry_uid -> [(aka_uid, ln, category)]  (Entity only)

    # Independent AKA name indexes — keyed by the AKA's own fn/ln, not the sdnEntry name.
    # Each entry: (sdn_uid, aka_uid, fn, ln, category)
    aka_fn_direct = defaultdict(list)
    aka_ln_direct = defaultdict(list)
    aka_fn_sdx    = defaultdict(list)
    aka_ln_sdx    = defaultdict(list)
    aka_fn_dm     = defaultdict(list)
    aka_ln_dm     = defaultdict(list)
    aka_fn_ny     = defaultdict(list)
    aka_ln_ny     = defaultdict(list)

    def _idx(uid, fn, ln, sdt, src):
        uid_to_sdntype[uid] = sdt
        if ln:
            k = ln.strip().lower()
            by_ln[k].append((uid, src))
            # Primary name stored only from sdnEntry; akaList must not overwrite
            if src == 'sdnEntry':
                uid_to_ln[uid] = k
                uid_to_ln_orig[uid] = ln.strip()
                uid_to_ln_nm[uid]   = _ph_norm_name(normalize(ln.strip(), strip_pat))
            sdx = _soundex(k)
            if sdx: sdx_by_ln[sdx].append((uid, src))
            for c in _dm_codes(k):  dm_by_ln[c].append((uid, src))
            for c in _nysiis_codes(k): ny_by_ln[c].append((uid, src))
        if fn:
            k = fn.strip().lower()
            by_fn[k].append((uid, src))
            if src == 'sdnEntry':
                uid_to_fn[uid] = k
                uid_to_fn_orig[uid] = fn.strip()
                uid_to_fn_nm[uid]   = _ph_norm_name(normalize(fn.strip(), strip_pat))
            sdx = _soundex(k)
            if sdx: sdx_by_fn[sdx].append((uid, src))
            for c in _dm_codes(k):  dm_by_fn[c].append((uid, src))
            for c in _nysiis_codes(k): ny_by_fn[c].append((uid, src))

    for uid, fn, ln, sdt in sdn_rows:
        _idx(uid, fn, ln, sdt, 'sdnEntry')
    for uid, aka_uid, fn, ln, category, sdt in aka_rows:
        _idx(uid, fn, ln, sdt, 'akaList')
        if sdt == 'Individual':
            aka_by_sdn[uid].append((aka_uid, fn, ln, category))
        elif sdt == 'Entity':
            entity_aka_by_sdn[uid].append((aka_uid, ln, category))
        # Populate AKA-specific indexes (keyed by the AKA's own fn/ln)
        # Only for Individual parents — Entity AKA names use org-name word rules (Pass 3b).
        if sdt != 'Individual':
            continue
        entry = (uid, aka_uid, fn, ln, category)
        if fn:
            k = fn.strip().lower()
            aka_fn_direct[k].append(entry)
            sdx = _soundex(k)
            if sdx: aka_fn_sdx[sdx].append(entry)
            for c in _dm_codes(k):    aka_fn_dm[c].append(entry)
            for c in _nysiis_codes(k): aka_fn_ny[c].append(entry)
        if ln:
            k = ln.strip().lower()
            aka_ln_direct[k].append(entry)
            sdx = _soundex(k)
            if sdx: aka_ln_sdx[sdx].append(entry)
            for c in _dm_codes(k):    aka_ln_dm[c].append(entry)
            for c in _nysiis_codes(k): aka_ln_ny[c].append(entry)

    # Full sdnEntry list for compliance coverage (excludes akaList-only rows)
    # Each element: (uid, fn_orig, ln_orig, sdntype)
    all_sdn_entries = [
        (uid,
         fn.strip() if fn else None,
         ln.strip() if ln else None,
         sdt)
        for uid, fn, ln, sdt in sdn_rows
    ]

    print(f"  {len(sdn_rows):,} sdnEntry + {len(aka_rows):,} akaList rows indexed.")
    return dict(
        by_ln=by_ln, by_fn=by_fn,
        sdx_by_ln=sdx_by_ln, sdx_by_fn=sdx_by_fn,
        dm_by_ln=dm_by_ln, dm_by_fn=dm_by_fn,
        ny_by_ln=ny_by_ln, ny_by_fn=ny_by_fn,
        uid_to_ln=uid_to_ln, uid_to_fn=uid_to_fn,
        uid_to_ln_orig=uid_to_ln_orig, uid_to_fn_orig=uid_to_fn_orig,
        uid_to_ln_nm=uid_to_ln_nm, uid_to_fn_nm=uid_to_fn_nm,
        uid_to_sdntype=uid_to_sdntype,
        all_sdn_entries=all_sdn_entries,
        aka_by_sdn=dict(aka_by_sdn),
        entity_aka_by_sdn=dict(entity_aka_by_sdn),
        aka_fn_direct=dict(aka_fn_direct),
        aka_ln_direct=dict(aka_ln_direct),
        aka_fn_sdx=dict(aka_fn_sdx), aka_ln_sdx=dict(aka_ln_sdx),
        aka_fn_dm=dict(aka_fn_dm),   aka_ln_dm=dict(aka_ln_dm),
        aka_fn_ny=dict(aka_fn_ny),   aka_ln_ny=dict(aka_ln_ny),
    )


@dataclass
class SdnAddress:
    sdn_entry_uid:     int
    sdn_addr_uid:      int
    # Raw values (as stored in SDN DB)
    address1:          Optional[str]
    address2:          Optional[str]
    address3:          Optional[str]
    city:              Optional[str]
    state_province:    Optional[str]
    postal_code:       Optional[str]
    country:           Optional[str]
    # Normalized values (USPS expansion + punct strip + abbrev expansion)
    address1_nm:       str
    address2_nm:       str
    address3_nm:       str
    city_nm:           str
    state_province_nm: str
    postal_code_nm:    str
    country_nm:        str


def load_sdn_addresses(conn, abbrev_map: dict, strip_pat: re.Pattern,
                       sdn_limit: int = None) -> tuple:
    """
    Returns (addresses: List[SdnAddress], word_index: dict).
    Loads raw SDN address values and normalizes them in Python using the same
    rules as the input side (USPS expansion, state/country abbreviation expansion,
    punct strip).  word_index: normalized word -> set of indices into addresses
    list (words > 2 chars from all normalized address + city + country fields).
    """
    if sdn_limit:
        print(f"Loading SDN address records — LIMIT {sdn_limit:,} SDN entries...")
        _uid_subq = f"SELECT TOP({sdn_limit}) uid FROM dbo.sdnEntry ORDER BY uid"
        rows = conn.cursor().execute(f"""
            SELECT e.uid, a.uid,
                   a.address1,    a.address2,    a.address3,
                   a.city,        a.stateOrProvince, a.postalCode, a.country
            FROM   dbo.addressList a
            JOIN   dbo.sdnEntry_addressList ea ON ea.addressList_uid = a.uid
            JOIN   dbo.sdnEntry e ON e.uid = ea.sdnEntry_uid
            WHERE  e.uid IN ({_uid_subq})
        """).fetchall()
    else:
        print("Loading SDN address records...")
        rows = conn.cursor().execute("""
            SELECT e.uid, a.uid,
                   a.address1,    a.address2,    a.address3,
                   a.city,        a.stateOrProvince, a.postalCode, a.country
            FROM   dbo.addressList a
            JOIN   dbo.sdnEntry_addressList ea ON ea.addressList_uid = a.uid
            JOIN   dbo.sdnEntry e ON e.uid = ea.sdnEntry_uid
        """).fetchall()

    addresses  = []
    word_index = defaultdict(set)

    for i, (sdn_uid, addr_uid, a1, a2, a3, city, state, postal, country) in enumerate(rows):
        a1_nm  = expand_address_nm(a1    or '', abbrev_map, strip_pat)
        a2_nm  = expand_address_nm(a2    or '', abbrev_map, strip_pat)
        a3_nm  = expand_address_nm(a3    or '', abbrev_map, strip_pat)
        cit_nm = expand_address_nm(city  or '', abbrev_map, strip_pat)
        st_nm  = normalize_state(state   or '', strip_pat)
        pc_nm  = normalize(postal        or '', strip_pat)
        co_nm  = normalize_country(country or '', strip_pat)

        addresses.append(SdnAddress(
            sdn_entry_uid=sdn_uid,  sdn_addr_uid=addr_uid,
            address1=_s(a1),        address2=_s(a2),        address3=_s(a3),
            city=_s(city),          state_province=_s(state),
            postal_code=_s(postal), country=_s(country),
            address1_nm=a1_nm,      address2_nm=a2_nm,      address3_nm=a3_nm,
            city_nm=cit_nm,         state_province_nm=st_nm,
            postal_code_nm=pc_nm,   country_nm=co_nm,
        ))
        for w in a1_nm.split():
            if len(w) > 2: word_index[w].add(i)
        for w in a2_nm.split():
            if len(w) > 2: word_index[w].add(i)
        for w in a3_nm.split():
            if len(w) > 2: word_index[w].add(i)
        for w in cit_nm.split():
            if len(w) > 2: word_index[w].add(i)
        for w in co_nm.split():
            if len(w) > 2: word_index[w].add(i)

    print(f"  {len(addresses):,} SDN address records loaded.")
    return addresses, dict(word_index)


def load_sdn_remarks(conn, strip_pat: re.Pattern, sdn_limit: int = None,
                     entity_map: dict = None) -> tuple:
    """
    Parse SDN remarks for 'Linked to:' name strings and phone numbers.

    Returns:
        linked_to_by_uid : dict[int, list[tuple]]
            uid -> [(occurrence, raw_text, nm_text, nm_text_expanded), ...]
            occurrence is 1-based; nm_text is lowercased + whitespace-collapsed raw_text;
            nm_text_expanded is the entity-suffix-expanded, normalized (uppercase) form
            of raw_text -- comparable to entity_name_nm / SDNOrgName_NM for entity matches.
        phones_by_uid : dict[int, list[tuple]]
            uid -> [(raw_phone, digits), ...]
        lt_word_index : dict[str, set[int]]
            normalized word (len > 2) -> set of uids that have that word in a Linked-to string
        phone_last7_idx : dict[str, set[int]]
            last-7 digit string -> set of uids that have a phone whose last 7 digits match
    """
    if sdn_limit:
        print(f"Loading SDN remarks (Linked-to + Phone) — LIMIT {sdn_limit:,} SDN entries...")
        _uid_subq = f"SELECT TOP({sdn_limit}) uid FROM dbo.sdnEntry ORDER BY uid"
        rows = conn.cursor().execute(
            f"SELECT uid, remarks FROM dbo.sdnEntry"
            f" WHERE remarks IS NOT NULL AND uid IN ({_uid_subq})"
        ).fetchall()
    else:
        print("Loading SDN remarks (Linked-to + Phone)...")
        rows = conn.cursor().execute(
            "SELECT uid, remarks FROM dbo.sdnEntry WHERE remarks IS NOT NULL"
        ).fetchall()

    linked_to_by_uid: dict = {}
    phones_by_uid:    dict = {}
    lt_word_index    = defaultdict(set)
    phone_last7_idx  = defaultdict(set)

    for uid, remarks in rows:
        if not remarks:
            continue

        # --- Linked-to occurrences ---
        lt_list = []
        for i, m in enumerate(_LINKED_TO_PAT.finditer(remarks), 1):
            raw = m.group(1).strip()
            if not raw:
                continue
            nm = re.sub(r'\s+', ' ', raw.lower()).strip()
            nm_expanded = _ph_norm_name(expand_entity_nm(raw, strip_pat, entity_map))
            lt_list.append((i, raw, nm, nm_expanded))
            for w in nm.split():
                if len(w) > 2:
                    lt_word_index[w].add(uid)
        if lt_list:
            linked_to_by_uid[uid] = lt_list

        # --- Phone numbers ---
        ph_list = []
        for m in _PHONE_FIELD_PAT.finditer(remarks):
            raw    = m.group(1).strip()
            digits = re.sub(r'\D', '', raw)
            if len(digits) < 7:
                continue
            ph_list.append((raw, digits))
            phone_last7_idx[digits[-7:]].add(uid)
        if ph_list:
            phones_by_uid[uid] = ph_list

    total_lt = sum(len(v) for v in linked_to_by_uid.values())
    total_ph = sum(len(v) for v in phones_by_uid.values())
    print(f"  {len(linked_to_by_uid):,} SDN entries with Linked-to text "
          f"({total_lt:,} occurrences).")
    print(f"  {len(phones_by_uid):,} SDN entries with phone numbers "
          f"({total_ph:,} occurrences).")
    return (linked_to_by_uid, phones_by_uid,
            dict(lt_word_index), dict(phone_last7_idx))


# ---------------------------------------------------------------------------
# Comparison engine
#
# A comparison row is a 18-tuple:
#   (sdn_uid, sdn_source, sdn_field, sdn_full_text,
#    input_field, input_value,
#    level,           -- 'full' or 'word'
#    inp_word_pos, sdn_word_pos, inp_word, sdn_word,
#    method, is_match, match_score,
#    edit_dist, edit_dist_sim, jw_dist, jw_sim)
# ---------------------------------------------------------------------------

_METHODS = ['Direct', 'Soundex', 'Double-Metaphone', 'NYSIIS']


def _compare_two(a: str, b: str, scores_cfg: dict, use_phonetic: bool = True) -> list:
    """
    Compare two lowercase strings.
    When use_phonetic=False, returns only the Direct method row.
    JW is computed once and shared across all method rows.
    """
    if not a or not b:
        return []

    ed, eds, jwd, jws = _score(a, b)

    is_match = (a == b)
    results = [('Direct', is_match,
                scores_cfg.get('Direct', 0) if is_match else 0,
                ed, eds, jwd, jws)]

    if not use_phonetic:
        return results

    # Soundex
    sa, sb = _soundex(a), _soundex(b)
    is_match = bool(sa and sb and sa == sb)
    results.append(('Soundex', is_match,
                    scores_cfg.get('Soundex', 0) if is_match else 0,
                    ed, eds, jwd, jws))

    # Double-Metaphone
    if _DM_AVAILABLE:
        dma, dmb = _dm_codes(a), _dm_codes(b)
        is_match = bool(dma and dmb and dma & dmb)
    else:
        is_match = False
    results.append(('Double-Metaphone', is_match,
                    scores_cfg.get('Double-Metaphone', 0) if is_match else 0,
                    ed, eds, jwd, jws))

    # NYSIIS
    nya, nyb = _nysiis_codes(a), _nysiis_codes(b)
    is_match = bool(nya and nyb and nya & nyb)
    results.append(('NYSIIS', is_match,
                    scores_cfg.get('NYSIIS', 0) if is_match else 0,
                    ed, eds, jwd, jws))

    return results


def _make_rows(sdn_uid, sdn_source, sdn_field, sdn_full_text,
               input_field, input_value,
               level, inp_pos, sdn_pos, inp_word, sdn_word,
               method_results) -> list:
    """Pack method_results into comparison row tuples."""
    rows = []
    for method, is_match, score, ed, eds, jwd, jws in method_results:
        rows.append((
            sdn_uid, sdn_source, sdn_field, sdn_full_text,
            input_field, input_value,
            level, inp_pos, sdn_pos, inp_word, sdn_word,
            method, 1 if is_match else 0, score,
            ed if ed != '' else None,
            eds if eds != '' else None,
            jwd if jwd != '' else None,
            jws if jws != '' else None,
        ))
    return rows


def compare_pair(sdn_uid: int, sdn_source: str, sdn_field: str, sdn_full_text: str,
                 input_field: str, input_value: str,
                 scores_cfg: dict, use_phonetic: bool = True) -> list:
    """
    Generate all comparison rows for one (input_value, sdn_value) field pair.
    When use_phonetic=False: 1 full-text row + 1 row per word pair (Direct only).
    When use_phonetic=True:  4 full-text rows + 4 rows per word pair.
    """
    iv = input_value.lower().strip()
    sv = sdn_full_text.lower().strip()
    if not iv or not sv:
        return []

    rows = []

    ft_results = _compare_two(iv, sv, scores_cfg, use_phonetic)
    rows += _make_rows(sdn_uid, sdn_source, sdn_field, sdn_full_text,
                       input_field, input_value,
                       'full', None, None, None, None,
                       ft_results)

    inp_words = iv.split()
    sdn_words = sv.split()

    for i, iw in enumerate(inp_words, 1):
        for j, sw in enumerate(sdn_words, 1):
            wd_results = _compare_two(iw, sw, scores_cfg, use_phonetic)
            rows += _make_rows(sdn_uid, sdn_source, sdn_field, sdn_full_text,
                               input_field, input_value,
                               'word', i, j, iw, sw,
                               wd_results)

    return rows


# ---------------------------------------------------------------------------
# Name candidate lookup  (returns candidate set)
# Each element: (uid, source, 'fn'|'ln')
# ---------------------------------------------------------------------------

def _name_candidates(key: str, idx: dict, field: str,
                     use_phonetic: bool = True) -> set:
    """
    Find (uid, source) pairs for key against the given field ('fn' or 'ln').
    When use_phonetic=False, only the direct exact-match index is consulted.
    """
    if not key:
        return set()
    candidates = set()

    for uid, src in idx[f'by_{field}'].get(key.lower(), []):
        candidates.add((uid, src))

    if not use_phonetic:
        return candidates

    sdx = _soundex(key)
    if sdx:
        for uid, src in idx[f'sdx_by_{field}'].get(sdx, []):
            candidates.add((uid, src))
    for code in _dm_codes(key):
        for uid, src in idx[f'dm_by_{field}'].get(code, []):
            candidates.add((uid, src))
    for code in _nysiis_codes(key):
        for uid, src in idx[f'ny_by_{field}'].get(code, []):
            candidates.add((uid, src))

    return candidates


def _aka_candidates(fn_nm: str, mn_nm: str, ln_nm: str,
                    name_idx: dict, use_phonetic: bool) -> dict:
    """
    Find all AKA entries that are candidates for the given input name.
    Completely independent of sdnEntry candidate status.
    Returns dict: (sdn_uid, aka_uid) -> (aka_fn, aka_ln, aka_cat)
    Uses the AKA-specific indexes (keyed by the AKA's own fn/ln, not sdnEntry names).
    """
    candidates: dict = {}

    def _add(entries):
        for sdn_uid, aka_uid, fn, ln, cat in entries:
            candidates.setdefault((sdn_uid, aka_uid), (fn, ln, cat))

    for val, field in [
        (fn_nm,                         'fn'),
        (ln_nm,                         'ln'),
        ((mn_nm + ' ' + ln_nm).strip(), 'ln'),
        ((fn_nm + ' ' + mn_nm).strip(), 'fn'),
    ]:
        if not val:
            continue
        k = val.lower()
        if field == 'fn':
            _add(name_idx['aka_fn_direct'].get(k, []))
            if use_phonetic:
                sdx = _soundex(k)
                if sdx: _add(name_idx['aka_fn_sdx'].get(sdx, []))
                for c in _dm_codes(k):    _add(name_idx['aka_fn_dm'].get(c, []))
                for c in _nysiis_codes(k): _add(name_idx['aka_fn_ny'].get(c, []))
        else:
            _add(name_idx['aka_ln_direct'].get(k, []))
            if use_phonetic:
                sdx = _soundex(k)
                if sdx: _add(name_idx['aka_ln_sdx'].get(sdx, []))
                for c in _dm_codes(k):    _add(name_idx['aka_ln_dm'].get(c, []))
                for c in _nysiis_codes(k): _add(name_idx['aka_ln_ny'].get(c, []))

    return candidates


# ---------------------------------------------------------------------------
# Name comparison field pairs
#
# Each entry: (input_field_label, input_value_fn, sdn_field, sdn_value_key)
# input_value_fn takes an InputRecord and returns the input string.
# sdn_value_key is 'fn' or 'ln' -- which SDN field to compare against.
# ---------------------------------------------------------------------------

NAME_FIELD_PAIRS = [
    # input_field_label,               input_val_fn,                                sdn_field, sdn_key
    ('FirstName_NM',            lambda r: r.first_name_nm,                               'firstName', 'fn'),
    ('LastName_NM',             lambda r: r.last_name_nm,                                'lastName',  'ln'),
    ('FirstName_MiddleName_NM', lambda r: (r.first_name_nm + ' ' + r.middle_name_nm).strip(), 'firstName', 'fn'),
    ('MiddleName_LastName_NM',  lambda r: (r.middle_name_nm + ' ' + r.last_name_nm).strip(),  'lastName',  'ln'),
    ('EntityName_NM',           lambda r: r.entity_name_nm,                              'lastName',  'ln'),
]

NAME_LOOKUP_KEYS = {
    'FirstName_NM':             lambda r: r.first_name_nm,
    'LastName_NM':              lambda r: r.last_name_nm,
    'FirstName_MiddleName_NM':  lambda r: (r.first_name_nm + ' ' + r.middle_name_nm).strip(),
    'MiddleName_LastName_NM':   lambda r: (r.middle_name_nm + ' ' + r.last_name_nm).strip(),
    'EntityName_NM':            lambda r: r.entity_name_nm,
}


def compare_names(rec: InputRecord, idx: dict, scores_cfg: dict,
                  use_phonetic: bool = True) -> list:
    """
    Find all SDN name candidates for this input record and generate
    comparison rows for every matching (input_field_pair, candidate) combo.
    """
    rows = []
    uid_to_ln      = idx['uid_to_ln']
    uid_to_fn      = idx['uid_to_fn']
    uid_to_ln_orig = idx['uid_to_ln_orig']
    uid_to_fn_orig = idx['uid_to_fn_orig']

    for label, inp_fn, sdn_field, sdn_key in NAME_FIELD_PAIRS:
        inp_val = inp_fn(rec)
        if not inp_val:
            continue

        candidates = _name_candidates(inp_val, idx, sdn_key, use_phonetic)

        for uid, src in candidates:
            if sdn_key == 'ln':
                sdn_val  = uid_to_ln.get(uid, '')
                sdn_orig = uid_to_ln_orig.get(uid, sdn_val)
            else:
                sdn_val  = uid_to_fn.get(uid, '')
                sdn_orig = uid_to_fn_orig.get(uid, sdn_val)

            if not sdn_val:
                continue

            rows += compare_pair(
                uid, src, sdn_field, sdn_orig,
                label, inp_val,
                scores_cfg, use_phonetic,
            )

    return rows


# ---------------------------------------------------------------------------
# Street-address match type lookup
#
# 15 ordered SDN-field combinations tried first for Direct match (IDs 1-15),
# then again for JW match (IDs 17-31).  ID 16/32 = No Match.
# The corresponding StreetAddressMatchType DB table is seeded from these lists.
# ---------------------------------------------------------------------------

_ADDR_COMBOS = [
    # Each tuple: SdnAddress normalized-field names to join for the SDN street string
    ('address1_nm', 'address2_nm', 'address3_nm'),   # 1 / 17
    ('address1_nm', 'address3_nm', 'address2_nm'),   # 2 / 18
    ('address2_nm', 'address3_nm', 'address1_nm'),   # 3 / 19
    ('address2_nm', 'address1_nm', 'address3_nm'),   # 4 / 20
    ('address3_nm', 'address1_nm', 'address2_nm'),   # 5 / 21
    ('address3_nm', 'address2_nm', 'address1_nm'),   # 6 / 22
    ('address1_nm', 'address2_nm'),                  # 7 / 23
    ('address2_nm', 'address1_nm'),                  # 8 / 24
    ('address1_nm', 'address3_nm'),                  # 9 / 25
    ('address3_nm', 'address1_nm'),                  # 10 / 26
    ('address2_nm', 'address3_nm'),                  # 11 / 27
    ('address3_nm', 'address2_nm'),                  # 12 / 28
    ('address1_nm',),                                # 13 / 29
    ('address2_nm',),                                # 14 / 30
    ('address3_nm',),                                # 15 / 31
]

_ADDR_COMBO_LABELS = [
    'Address1 + Address2 + Address3',
    'Address1 + Address3 + Address2',
    'Address2 + Address3 + Address1',
    'Address2 + Address1 + Address3',
    'Address3 + Address1 + Address2',
    'Address3 + Address2 + Address1',
    'Address1 + Address2',
    'Address2 + Address1',
    'Address1 + Address3',
    'Address3 + Address1',
    'Address2 + Address3',
    'Address3 + Address2',
    'Address1',
    'Address2',
    'Address3',
]

# Static seed data: 16 rows (JaroWinkler 17-32 only; IDs preserved for FK compatibility)
_ADDR_MATCH_TYPE_ROWS = (
    [(i,  'JaroWinkler', _ADDR_COMBO_LABELS[i - 17]) for i in range(17, 32)] +
    [(32, 'JaroWinkler', 'No Match')]
)


def _build_sdn_street(sdn_addr: SdnAddress, fields: tuple) -> str:
    """Join non-empty normalized SDN address fields for one combo."""
    return ' '.join(getattr(sdn_addr, f) for f in fields
                    if getattr(sdn_addr, f, '')).strip()


def _find_street_jw_match(mailing: str, sdn_addr: SdnAddress,
                           threshold: float) -> int:
    """
    Try each combo in _ADDR_COMBOS for a Jaro-Winkler similarity >= threshold
    against the normalised input mailing string.  Stops at first qualifying combo.
    Returns match-type ID 17-31, or 32 (No Match) if none qualify.
    """
    if not mailing:
        return 32
    ml = mailing.lower()
    for i, fields in enumerate(_ADDR_COMBOS, 17):
        sdn_val = _build_sdn_street(sdn_addr, fields).lower()
        if sdn_val:
            if _jaro_winkler_fast(ml, sdn_val) >= threshold:
                return i
    return 32


def _geo_addr_score(inp_nm: str, sdn_nm: str) -> float:
    """Return jw_similarity_pct for a single geo field pair."""
    iv = (inp_nm or '').lower().strip()
    sv = (sdn_nm or '').lower().strip()
    if not iv or not sv:
        return 0.00
    return round(_jaro_winkler_fast(iv, sv) * 100, 2)


# ---------------------------------------------------------------------------
# Address comparison  (legacy — compare_addresses() writes to MatchingResults_v2)
#
# Input address field combos  (label, value_fn)
# SDN address field combos    (label, SdnAddress attribute/combo names)
# ---------------------------------------------------------------------------

_INP_ADDR_COMBOS = [
    ('Address1_NM',        lambda r: r.address1_nm),
    ('Address2_NM',        lambda r: r.address2_nm),
    ('Address3_NM',        lambda r: r.address3_nm),
    ('Address1_2_NM',      lambda r: (r.address1_nm + ' ' + r.address2_nm).strip()),
    ('Address1_3_NM',      lambda r: (r.address1_nm + ' ' + r.address3_nm).strip()),
    ('Address2_3_NM',      lambda r: (r.address2_nm + ' ' + r.address3_nm).strip()),
    ('Address1_2_3_NM',    lambda r: ' '.join(filter(None, [r.address1_nm, r.address2_nm, r.address3_nm]))),
]

_SDN_ADDR_COMBOS = [
    ('address1_nm',        lambda a: a.address1),
    ('address2_nm',        lambda a: a.address2),
    ('address3_nm',        lambda a: a.address3),
    ('address1_2_nm',      lambda a: (a.address1 + ' ' + a.address2).strip()),
    ('address1_3_nm',      lambda a: (a.address1 + ' ' + a.address3).strip()),
    ('address2_3_nm',      lambda a: (a.address2 + ' ' + a.address3).strip()),
    ('address1_2_3_nm',    lambda a: ' '.join(filter(None, [a.address1, a.address2, a.address3]))),
]

_GEO_PAIRS = [
    # (input_field_label, inp_val_fn, sdn_field_label, sdn_val_fn)
    ('City_NM',       lambda r: r.city_nm,        'city_nm',       lambda a: a.city),
    ('Region_NM',     lambda r: r.region_nm,      'region_nm',     lambda a: a.region),
    ('PostalCode_NM', lambda r: r.postal_code_nm, 'postalCode_nm', lambda a: a.postal),
    ('Country_NM',    lambda r: r.country_nm,     'country_nm',    lambda a: a.country),
]


def compare_addresses(rec: InputRecord, addresses: list, word_index: dict,
                      scores_cfg: dict, max_candidates: int,
                      use_phonetic: bool = True) -> list:
    """
    Find SDN address candidates using word index, then compare all
    address field combinations and geo fields.
    """
    rows = []

    # Collect candidate address indices via shared words
    candidate_indices = set()
    for combo_label, inp_fn in _INP_ADDR_COMBOS[:3]:   # address1, 2, 3 only for lookup
        inp_val = inp_fn(rec)
        for word in inp_val.split():
            if len(word) > 2:
                candidate_indices.update(word_index.get(word, set()))

    # Also add candidates from city
    for word in rec.city_nm.split():
        if len(word) > 2:
            candidate_indices.update(word_index.get(word, set()))

    if not candidate_indices:
        return []

    # Limit to max_candidates most recently seen (arbitrary order -- improve later)
    candidate_indices = list(candidate_indices)[:max_candidates]

    for idx in candidate_indices:
        addr = addresses[idx]

        # Address line combos
        for inp_label, inp_fn in _INP_ADDR_COMBOS:
            inp_val = inp_fn(rec)
            if not inp_val:
                continue
            for sdn_label, sdn_fn in _SDN_ADDR_COMBOS:
                sdn_val = sdn_fn(addr)
                if not sdn_val:
                    continue
                rows += compare_pair(
                    addr.uid, 'addressList', sdn_label, sdn_val,
                    inp_label, inp_val,
                    scores_cfg, use_phonetic,
                )

        # Geo fields
        for inp_label, inp_fn, sdn_label, sdn_fn in _GEO_PAIRS:
            inp_val = inp_fn(rec)
            sdn_val = sdn_fn(addr)
            if not inp_val or not sdn_val:
                continue
            rows += compare_pair(
                addr.uid, 'addressList', sdn_label, sdn_val,
                inp_label, inp_val,
                scores_cfg, use_phonetic,
            )

    return rows


# ---------------------------------------------------------------------------
# Output DDL
# ---------------------------------------------------------------------------

_DDL_RUNLOG = """
IF OBJECT_ID(N'[{s}].[MatchingResults_v2_RunLog]', N'U') IS NULL
CREATE TABLE [{s}].[MatchingResults_v2_RunLog] (
    run_id              INT           NOT NULL IDENTITY(1,1),
    run_date            DATETIME      NOT NULL CONSTRAINT DF_RunLog_run_date   DEFAULT GETDATE(),
    sdn_publish_info_id INT           NULL,
    sdn_publish_date    DATE          NULL,
    input_source        NVARCHAR(500) NULL,
    records_checked     INT           NOT NULL CONSTRAINT DF_RunLog_records    DEFAULT 0,
    total_rows_written  BIGINT        NOT NULL CONSTRAINT DF_RunLog_total_rows DEFAULT 0,
    name_candidates     INT           NOT NULL CONSTRAINT DF_RunLog_name_cands DEFAULT 0,
    address_candidates  INT           NOT NULL CONSTRAINT DF_RunLog_addr_cands DEFAULT 0,
    CONSTRAINT PK_RunLog PRIMARY KEY (run_id)
);
"""

_DDL_INPUT = """
IF OBJECT_ID(N'[{s}].[MatchingInput_v2]', N'U') IS NULL
CREATE TABLE [{s}].[MatchingInput_v2] (
    input_id        BIGINT        NOT NULL IDENTITY PRIMARY KEY,
    run_id          INT           NOT NULL,
    entity_type     NVARCHAR(50)  NULL,
    entity_name     NVARCHAR(900) NULL,
    first_name      NVARCHAR(255) NULL,
    middle_name     NVARCHAR(255) NULL,
    last_name       NVARCHAR(500) NULL,
    address1        NVARCHAR(500) NULL,
    address2        NVARCHAR(500) NULL,
    address3        NVARCHAR(500) NULL,
    city            NVARCHAR(200) NULL,
    region          NVARCHAR(100) NULL,
    postal_code     VARCHAR(20)   NULL,
    country         NVARCHAR(100) NULL,
    entity_name_nm  NVARCHAR(900) NULL,
    first_name_nm   NVARCHAR(255) NULL,
    middle_name_nm  NVARCHAR(255) NULL,
    last_name_nm    NVARCHAR(500) NULL,
    address1_nm     NVARCHAR(500) NULL,
    address2_nm     NVARCHAR(500) NULL,
    address3_nm     NVARCHAR(500) NULL,
    city_nm         NVARCHAR(200) NULL,
    region_nm       NVARCHAR(100) NULL,
    postal_code_nm  VARCHAR(20)   NULL,
    country_nm      NVARCHAR(100) NULL,
    INDEX IX_MI_run (run_id)
);
"""

_DDL_RESULTS = """
IF OBJECT_ID(N'[{s}].[MatchingResults_v2]', N'U') IS NULL
CREATE TABLE [{s}].[MatchingResults_v2] (
    ID                       BIGINT        NOT NULL IDENTITY PRIMARY KEY,
    run_id                   INT           NOT NULL,
    input_id                 BIGINT        NOT NULL,
    sdn_uid                  INT           NOT NULL,
    sdn_source               VARCHAR(20)   NOT NULL,  -- sdnEntry, akaList, addressList
    sdn_field                VARCHAR(50)   NOT NULL,  -- field name in SDN
    sdn_full_text            NVARCHAR(1000) NULL,     -- full text of compared SDN field
    input_field              VARCHAR(100)  NOT NULL,  -- input field/combo label
    input_value              NVARCHAR(1000) NULL,     -- actual input value used
    comparison_level         VARCHAR(4)    NOT NULL,  -- 'full' or 'word'
    input_word_pos           TINYINT       NULL,
    sdn_word_pos             TINYINT       NULL,
    input_word               NVARCHAR(255) NULL,
    sdn_word                 NVARCHAR(255) NULL,
    method                   VARCHAR(20)   NOT NULL,
    is_match                 BIT           NULL,
    match_score              INT           NULL,
    jaro_winkler_distance    DECIMAL(8,6)  NULL,
    jaro_winkler_similarity  DECIMAL(8,6)  NULL,
    INDEX IX_MR_run    (run_id),
    INDEX IX_MR_input  (input_id),
    INDEX IX_MR_sdn    (sdn_uid),
    INDEX IX_MR_method (method, is_match)
);
"""


def setup_output_tables(conn, schema: str, drop: bool):
    cur = conn.cursor()
    cur.execute(_DDL_RUNLOG.replace('{s}', schema))
    if drop:
        # Drop MatchingResults_Address first (FK -> StreetAddressMatchType + RunLog)
        cur.execute(f"IF OBJECT_ID(N'[{schema}].[MatchingResults_Address]', N'U') IS NOT NULL "
                    f"DROP TABLE [{schema}].[MatchingResults_Address];")
        # Drop StreetAddressMatchType (referenced by MatchingResults_Address)
        cur.execute(f"IF OBJECT_ID(N'[{schema}].[StreetAddressMatchType]', N'U') IS NOT NULL "
                    f"DROP TABLE [{schema}].[StreetAddressMatchType];")
        # Drop remaining child tables (all reference RunLog via FK).
        # Includes legacy per-pair NoMatch tables that have been replaced
        # by MatchingResults_NoMatch.
        for tbl in ('Matching_Summary_Person', 'Matching_Summary_Org',
                    'MatchingResults_Phone',
                    'MatchingResults_NoMatch',
                    'MatchingResults_Address_NoMatch',
                    'MatchingResults_LinkedTo_NoMatch', 'MatchingResults_LinkedTo',
                    'MatchingResults_OrgName_AKA_NoMatch', 'MatchingResults_OrgName_AKA',
                    'MatchingResults_OrgName_NoMatch', 'MatchingResults_OrgName',
                    'MatchingResults_AKA_NoMatch', 'MatchingResults_AKA',
                    'MatchingResults_Person_NoMatch', 'MatchingResults_Person_Full'):
            cur.execute(f"IF OBJECT_ID(N'[{schema}].[{tbl}]', N'U') IS NOT NULL "
                        f"DROP TABLE [{schema}].[{tbl}];")
    # Full-match result tables
    cur.execute(_DDL_FULL.replace('{s}', schema))
    cur.execute(_DDL_AKA.replace('{s}', schema))
    cur.execute(_DDL_ORG.replace('{s}', schema))
    cur.execute(_DDL_ORG_AKA.replace('{s}', schema))
    # Address tables: lookup first (no FK dependency), then results table
    cur.execute(_DDL_ADDR_MATCH_TYPE.replace('{s}', schema))
    cur.execute(_DDL_ADDR_FULL.replace('{s}', schema))
    # Remarks-derived tables
    cur.execute(_DDL_LINKED_TO.replace('{s}', schema))
    cur.execute(_DDL_PHONE.replace('{s}', schema))
    # No-match log (one row per unmatched input record)
    cur.execute(_DDL_NO_MATCH_LOG.replace('{s}', schema))
    # Post-processing summary tables
    cur.execute(_DDL_SUMMARY_PERSON.replace('{s}', schema))
    cur.execute(_DDL_SUMMARY_ORG.replace('{s}', schema))
    conn.commit()
    seed_addr_match_types(conn, schema)


# ---------------------------------------------------------------------------
# ScreeningInput  DDL  (lives in the SDN database, not SDNReporting)
# Holds customer / principal records to be screened against the SDN list.
# Mirrors the column set loaded from dbo.Principals_Alpha.
# ---------------------------------------------------------------------------

_DDL_SCREENING_INPUT = """
IF OBJECT_ID(N'[{s}].[ScreeningInput]', N'U') IS NULL
BEGIN
    CREATE TABLE [{s}].[ScreeningInput] (
        ScreeningInput_ID   BIGINT        NOT NULL IDENTITY(1,1),
        SDN_Customer_ID     INT           NULL,
        Upload_Date         DATETIME      NULL,

        -- Entity identifiers
        ENTITY_NAME         NVARCHAR(900) NULL,
        ENTITY_NUM          VARCHAR(100)  NULL,

        -- Individual name fields
        FIRST_NAME          NVARCHAR(255) NULL,
        MIDDLE_NAME         NVARCHAR(255) NULL,
        LAST_NAME           NVARCHAR(500) NULL,

        -- Organization name (used as last_name when no individual name present)
        ORG_NAME            NVARCHAR(900) NULL,

        -- Address fields
        ADDRESS1            NVARCHAR(500) NULL,
        ADDRESS2            NVARCHAR(500) NULL,
        ADDRESS3            NVARCHAR(500) NULL,
        CITY                NVARCHAR(200) NULL,
        STATE               NVARCHAR(100) NULL,
        POSTAL_CODE         VARCHAR(20)   NULL,
        COUNTRY             NVARCHAR(100) NULL,

        -- Contact / source-system identifiers
        PHONE               VARCHAR(50)   NULL,
        Contact_ID          VARCHAR(255)  NULL,
        Entity_ID           VARCHAR(255)  NULL,

        CONSTRAINT PK_ScreeningInput PRIMARY KEY CLUSTERED (ScreeningInput_ID ASC)
    );
    CREATE INDEX IX_SI_Customer   ON [{s}].[ScreeningInput] (SDN_Customer_ID);
    CREATE INDEX IX_SI_Contact    ON [{s}].[ScreeningInput] (Contact_ID);
    CREATE INDEX IX_SI_Entity     ON [{s}].[ScreeningInput] (Entity_ID);
    CREATE INDEX IX_SI_UploadDate ON [{s}].[ScreeningInput] (Upload_Date);
    CREATE INDEX IX_SI_EntityName ON [{s}].[ScreeningInput] (ENTITY_NAME);
END;
"""


_DDL_ENTITY_TYPE_MAP = """
IF OBJECT_ID(N'[{s}].[EntityTypeMap]', N'U') IS NULL
BEGIN
    CREATE TABLE [{s}].[EntityTypeMap] (
        ID           INT           NOT NULL IDENTITY(1,1),
        Abbreviation NVARCHAR(50)  NULL,
        ExpandedForm NVARCHAR(200) NOT NULL,
        Position     VARCHAR(6)    NOT NULL,
        Notes        NVARCHAR(500) NULL,
        CONSTRAINT PK_EntityTypeMap PRIMARY KEY (ID),
        CONSTRAINT CK_EntityTypeMap_Pos CHECK (Position IN ('PREFIX','SUFFIX','BOTH'))
    );
END;
IF NOT EXISTS (SELECT 1 FROM [{s}].[EntityTypeMap])
BEGIN
    INSERT INTO [{s}].[EntityTypeMap] (Abbreviation, ExpandedForm, Position) VALUES
    -- US/UK/General suffix entries
    ('LLC',  'Limited Liability Company',             'BOTH'),
    ('LC',   'Limited Company',                       'SUFFIX'),
    ('INC',  'Incorporated',                          'SUFFIX'),
    ('CORP', 'Corporation',                           'SUFFIX'),
    ('LTD',  'Limited',                               'SUFFIX'),
    ('LP',   'Limited Partnership',                   'SUFFIX'),
    ('LLP',  'Limited Liability Partnership',         'SUFFIX'),
    ('LLLP', 'Limited Liability Limited Partnership', 'SUFFIX'),
    ('PC',   'Professional Corporation',              'SUFFIX'),
    ('PLLC', 'Professional Limited Liability Company','SUFFIX'),
    ('PA',   'Professional Association',              'SUFFIX'),
    ('PLC',  'Public Limited Company',                'SUFFIX'),
    ('CO',   'Company',                               'SUFFIX'),
    ('ASSOC','Association',                           'SUFFIX'),
    ('ASSN', 'Association',                           'SUFFIX'),
    ('BROS', 'Brothers',                              'SUFFIX'),
    ('INTL', 'International',                         'SUFFIX'),
    ('NATL', 'National',                              'SUFFIX'),
    ('MGMT', 'Management',                            'SUFFIX'),
    ('SVCS', 'Services',                              'SUFFIX'),
    ('SVC',  'Service',                               'SUFFIX'),
    ('TECH', 'Technology',                            'SUFFIX'),
    -- International suffix entries (German/Austrian/Swiss)
    ('GMBH', 'Gesellschaft mit Beschraenkter Haftung','SUFFIX'),
    ('AG',   'Aktiengesellschaft',                    'SUFFIX'),
    ('KG',   'Kommanditgesellschaft',                 'SUFFIX'),
    ('OHG',  'Offene Handelsgesellschaft',            'SUFFIX'),
    ('UG',   'Unternehmergesellschaft',               'SUFFIX'),
    ('EV',   'Eingetragener Verein',                  'SUFFIX'),
    -- French
    ('SA',   'Societe Anonyme',                       'SUFFIX'),
    ('SARL', 'Societe a Responsabilite Limitee',      'SUFFIX'),
    ('SAS',  'Societe par Actions Simplifiee',        'SUFFIX'),
    ('SNC',  'Societe en Nom Collectif',              'SUFFIX'),
    ('SC',   'Societe Civile',                        'SUFFIX'),
    -- Italian
    ('SPA',  'Societa per Azioni',                    'SUFFIX'),
    ('SRL',  'Societa a Responsabilita Limitata',     'SUFFIX'),
    -- Spanish/Portuguese
    ('SL',   'Sociedad Limitada',                     'SUFFIX'),
    ('LTDA', 'Limitada',                              'SUFFIX'),
    ('LDA',  'Limitada',                              'SUFFIX'),
    -- Dutch/Belgian
    ('BV',   'Besloten Vennootschap',                 'SUFFIX'),
    ('NV',   'Naamloze Vennootschap',                 'SUFFIX'),
    ('VOF',  'Vennootschap onder Firma',              'SUFFIX'),
    ('CV',   'Commanditaire Vennootschap',            'SUFFIX'),
    -- Nordic
    ('AB',   'Aktiebolag',                            'SUFFIX'),
    ('AS',   'Aksjeselskap',                          'SUFFIX'),
    ('OY',   'Osakeyhtioe',                           'SUFFIX'),
    -- Malaysian
    ('BHD',  'Berhad',                                'SUFFIX'),
    -- Japanese
    ('KK',   'Kabushiki Kaisha',                      'SUFFIX'),
    ('GK',   'Godo Kaisha',                           'SUFFIX'),
    -- Russian/CIS (suffix entries; BOTH for those that also appear as prefix)
    ('OAO',  'Otkrytoye Aktsionernoye Obshchestvo',  'BOTH'),
    ('ZAO',  'Zakrytoye Aktsionernoye Obshchestvo',  'BOTH'),
    ('OOO',  'Obshchestvo s Ogranichennoy Otvetstvennostyu', 'BOTH'),
    ('PAO',  'Publichnoye Aktsionernoye Obshchestvo', 'BOTH'),
    ('AO',   'Aktsionernoye Obshchestvo',             'BOTH'),
    -- Gulf/Middle East
    ('WLL',  'With Limited Liability',                'SUFFIX'),
    ('BSC',  'Bahraini Shareholding Company',         'SUFFIX'),
    ('KSC',  'Kuwaiti Shareholding Company',          'SUFFIX'),
    ('KSCC', 'Kuwaiti Shareholding Company Closed',   'SUFFIX'),
    ('PSC',  'Private Shareholding Company',          'SUFFIX'),
    -- Joint Stock variants (BOTH: appear as prefix in CIS/Eastern European convention)
    ('JSC',  'Joint Stock Company',                   'BOTH'),
    ('OJSC', 'Open Joint Stock Company',              'BOTH'),
    ('CJSC', 'Closed Joint Stock Company',            'BOTH'),
    ('PJSC', 'Public Joint Stock Company',            'BOTH');
END;
"""


def setup_sdn_input_table(conn, schema: str, drop: bool):
    """Create ScreeningInput in the SDN database if it does not already exist.
    When drop=True the table is dropped and recreated (data will be lost)."""
    cur = conn.cursor()
    if drop:
        cur.execute(f"IF OBJECT_ID(N'[{schema}].[ScreeningInput]', N'U') IS NOT NULL "
                    f"DROP TABLE [{schema}].[ScreeningInput];")
    cur.execute(_DDL_SCREENING_INPUT.replace('{s}', schema))
    cur.execute(_DDL_ENTITY_TYPE_MAP.replace('{s}', schema))
    conn.commit()


_DUMMY_SCREENING_INSERT_SQL = """
INSERT INTO [{s}].[ScreeningInput]
    (Upload_Date,
     ENTITY_NAME, ENTITY_NUM,
     FIRST_NAME,  MIDDLE_NAME, LAST_NAME,
     ADDRESS1,    ADDRESS2,    ADDRESS3,
     CITY,        STATE,       POSTAL_CODE, COUNTRY,
     Contact_ID,  Entity_ID)
VALUES
    (GETDATE(),
     ?,          'DUMMY',
     ?,           ?,           ?,
     ?,           ?,           ?,
     ?,           ?,           ?,           ?,
     ?,           ?)
"""  # 13 value placeholders (ENTITY_NUM and Upload_Date are literal / SQL function)


def insert_dummy_screening_rows(conn, schema: str, dummy_recs: list,
                                batch_size: int = 1000) -> None:
    """
    Persist dummy InputRecord objects as rows in [{schema}].[ScreeningInput].

    ENTITY_NUM is set to 'DUMMY' so rows are easy to identify and exclude from
    non-QA runs.  Contact_ID carries the category tag for Individual records
    (e.g. 'DUMMY-P-0001'); Entity_ID carries it for Entity records.

    Dummy records are already in memory for the current run; this function
    only handles the persistence side.
    """
    if not dummy_recs:
        return
    sql = _DUMMY_SCREENING_INSERT_SQL.replace('{s}', schema)
    rows = []
    for rec in dummy_recs:
        is_indiv = (rec.entity_type == 'Individual')
        rows.append((
            rec.entity_name if not is_indiv else None,  # ENTITY_NAME
            rec.first_name  if is_indiv else None,      # FIRST_NAME
            rec.middle_name if is_indiv else None,      # MIDDLE_NAME
            rec.last_name   if is_indiv else None,      # LAST_NAME
            rec.address1,                               # ADDRESS1
            rec.address2,                               # ADDRESS2
            rec.address3,                               # ADDRESS3
            rec.city,                                   # CITY
            rec.region,                                 # STATE
            rec.postal_code,                            # POSTAL_CODE
            rec.country,                                # COUNTRY
            rec.external_id if is_indiv else None,      # Contact_ID
            rec.external_id if not is_indiv else None,  # Entity_ID
        ))
    cur = conn.cursor()
    cur.fast_executemany = True
    for i in range(0, len(rows), batch_size):
        cur.executemany(sql, rows[i:i + batch_size])
    conn.commit()


def create_run(conn, schema: str, input_source: str,
               sdn_publish_info_id: Optional[int],
               sdn_publish_date: Optional[str]) -> int:
    cur = conn.cursor()
    cur.execute(
        f"INSERT INTO [{schema}].[MatchingResults_v2_RunLog] "
        f"(input_source, sdn_publish_info_id, sdn_publish_date) "
        f"OUTPUT INSERTED.run_id VALUES (?,?,?)",
        [input_source, sdn_publish_info_id, sdn_publish_date]
    )
    run_id = int(cur.fetchone()[0])
    conn.commit()
    return run_id


def update_run(conn, schema: str, run_id: int,
               principals: int, total_rows: int,
               name_cands: int, addr_cands: int):
    conn.cursor().execute(
        f"UPDATE [{schema}].[MatchingResults_v2_RunLog] "
        f"SET records_checked=?, total_rows_written=?, "
        f"    name_candidates=?, address_candidates=? "
        f"WHERE run_id=?",
        [principals, total_rows, name_cands, addr_cands, run_id]
    )
    conn.commit()


def insert_input_record(conn, schema: str, run_id: int,
                        rec: InputRecord) -> int:
    cur = conn.cursor()
    cur.execute(
        f"INSERT INTO [{schema}].[MatchingInput_v2] "
        f"(run_id, entity_type, entity_name, first_name, middle_name, last_name, "
        f"address1, address2, address3, city, region, postal_code, country, "
        f"entity_name_nm, first_name_nm, middle_name_nm, last_name_nm, "
        f"address1_nm, address2_nm, address3_nm, city_nm, region_nm, "
        f"postal_code_nm, country_nm) "
        f"OUTPUT INSERTED.input_id "
        f"VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [run_id,
         _s(rec.entity_type), _s(rec.entity_name),
         _s(rec.first_name), _s(rec.middle_name), _s(rec.last_name),
         _s(rec.address1), _s(rec.address2), _s(rec.address3),
         _s(rec.city), _s(rec.region), _s(rec.postal_code), _s(rec.country),
         rec.entity_name_nm, rec.first_name_nm, rec.middle_name_nm, rec.last_name_nm,
         rec.address1_nm, rec.address2_nm, rec.address3_nm,
         rec.city_nm, rec.region_nm, rec.postal_code_nm, rec.country_nm]
    )
    return int(cur.fetchone()[0])


def flush_results(conn, schema: str, run_id: int, input_id: int,
                  rows: list, batch_size: int = 1000):
    """Write comparison rows to MatchingResults_v2 in batches."""
    if not rows:
        return
    sql = (
        f"INSERT INTO [{schema}].[MatchingResults_v2] "
        f"(run_id, input_id, sdn_uid, sdn_source, sdn_field, sdn_full_text, "
        f"input_field, input_value, comparison_level, "
        f"input_word_pos, sdn_word_pos, input_word, sdn_word, "
        f"method, is_match, match_score, "
        f"jaro_winkler_distance, jaro_winkler_similarity) "
        f"VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
    )
    cur = conn.cursor()
    cur.fast_executemany = True
    tagged = [
        (run_id, input_id,
         r[0], r[1], r[2], r[3],     # sdn_uid, source, field, full_text
         r[4], r[5],                  # input_field, input_value
         r[6], r[7], r[8], r[9], r[10],  # level, positions, words
         r[11], r[12], r[13],         # method, is_match, match_score
         r[16], r[17])                # jw scores (indices unchanged)
        for r in rows
    ]
    for i in range(0, len(tagged), batch_size):
        cur.executemany(sql, tagged[i:i + batch_size])
    conn.commit()


# ---------------------------------------------------------------------------
# Input loading  (CSV or database)
# ---------------------------------------------------------------------------

_INPUT_COLS = [
    'EntityType', 'EntityName', 'FirstName', 'MiddleName', 'LastName',
    'Address1', 'Address2', 'Address3', 'City', 'Region', 'PostalCode', 'Country',
]


def _row_to_record(vals: list) -> InputRecord:
    return InputRecord(
        entity_type = _s(vals[0]),
        entity_name = _s(vals[1]),
        first_name  = _s(vals[2]),
        middle_name = _s(vals[3]),
        last_name   = _s(vals[4]),
        address1    = _s(vals[5]),
        address2    = _s(vals[6]),
        address3    = _s(vals[7]),
        city        = _s(vals[8]),
        region      = _s(vals[9]),
        postal_code = _s(vals[10]),
        country     = _s(vals[11]),
    )


def load_input_csv(path: str) -> list:
    import csv
    print(f"Loading input from CSV: {path}")
    recs = []
    with open(path, newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            vals = [row.get(c) for c in _INPUT_COLS]
            recs.append(_row_to_record(vals))
    print(f"  {len(recs):,} records loaded.")
    return recs


def load_input_db(server: str, database: str, table: str) -> list:
    col_clause = ', '.join(f'[{c}]' for c in _INPUT_COLS)
    sql = f"SELECT {col_clause} FROM {table}"
    print(f"Loading input from [{server}].[{database}].{table} ...")
    with pyodbc.connect(_conn_str(server, database)) as conn:
        rows = conn.cursor().execute(sql).fetchall()
    recs = [_row_to_record(list(r)) for r in rows]
    print(f"  {len(recs):,} records loaded.")
    return recs


def load_input_principals(server: str, database: str,
                          entity_name_filter: str = '*',
                          top_rows: int = None) -> list:
    """
    Load input records from dbo.Principals_Alpha.

    Column mapping:
      ENTITY_NAME   -> entity_name
      FIRST_NAME    -> first_name
      MIDDLE_NAME   -> middle_name
      LAST_NAME     -> last_name
      ORG_NAME      -> last_name  (fallback when all three name fields are NULL)
      ADDRESS1-3    -> address1-3
      CITY          -> city
      STATE         -> region
      POSTAL_CODE   -> postal_code
      COUNTRY       -> country
      ENTITY_NUM / POSITION_TYPE are loaded but not passed to InputRecord.

    Entity-type logic:
      'Individual'  when FIRST_NAME or LAST_NAME is non-null
      'Entity'      when all three name fields are null (ORG_NAME used as last_name)
    """
    params: list = []
    if entity_name_filter and entity_name_filter != '*':
        prefix = entity_name_filter.rstrip('%')
        where  = "WHERE ENTITY_NAME LIKE ?"
        params = [prefix + '%']
    else:
        where = ""

    top_clause = f"TOP {top_rows}" if top_rows else ""
    sql = f"""
        SELECT DISTINCT {top_clause}
               ENTITY_NAME, ENTITY_NUM, ORG_NAME,
               FIRST_NAME, MIDDLE_NAME, LAST_NAME,
               ADDRESS1, ADDRESS2, ADDRESS3,
               CITY, STATE, COUNTRY, POSTAL_CODE
        FROM   dbo.Principals_Alpha
        {where}
        ORDER BY ENTITY_NAME, ENTITY_NUM
    """

    print(f"Loading input from [{server}].[{database}].dbo.Principals_Alpha ...")
    if params:
        print(f"  Filter: ENTITY_NAME LIKE '{params[0]}'")

    with pyodbc.connect(_conn_str(server, database)) as conn:
        rows = conn.cursor().execute(sql, params).fetchall()

    recs = []
    for row in rows:
        entity_name = _s(row[0])
        # row[1] = ENTITY_NUM  — not used in InputRecord
        org_name    = _s(row[2])
        first_name  = _s(row[3])
        middle_name = _s(row[4])
        last_name   = _s(row[5])
        address1    = _s(row[6])
        address2    = _s(row[7])
        address3    = _s(row[8])
        city        = _s(row[9])
        region      = _s(row[10])   # STATE column
        country     = _s(row[11])
        postal_code = _s(row[12])

        # No individual name present — use ORG_NAME as last name for entity matching
        if not first_name and not middle_name and not last_name:
            last_name   = org_name
            entity_type = 'Entity'
        else:
            entity_type = 'Individual'

        entity_num = _s(row[1])   # ENTITY_NUM — external identifier
        recs.append(InputRecord(
            entity_type = entity_type,
            entity_name = entity_name,
            external_id = entity_num,
            first_name  = first_name,
            middle_name = middle_name,
            last_name   = last_name,
            address1    = address1,
            address2    = address2,
            address3    = address3,
            city        = city,
            region      = region,
            postal_code = postal_code,
            country     = country,
        ))

    print(f"  {len(recs):,} records loaded.")
    return recs


def load_input_screening(server: str, database: str, schema: str = 'dbo',
                         table: str = 'ScreeningInput',
                         top_rows: int = None,
                         start_row: int = None) -> list:
    """
    Load input records from [{schema}].[{table}] in the SDN database.

    Column mapping:
      ENTITY_NAME   -> entity_name
      ENTITY_NUM    -> (not used; external_id = ScreeningInput_ID)
      FIRST_NAME    -> first_name
      MIDDLE_NAME   -> middle_name
      LAST_NAME     -> last_name
      ORG_NAME      -> last_name  (fallback when all three name fields are NULL)
      ADDRESS1-3    -> address1-3
      CITY          -> city
      STATE         -> region
      POSTAL_CODE   -> postal_code
      COUNTRY       -> country
      Contact_ID    -> (stored in external_id when present, else ScreeningInput_ID)
      Entity_ID     -> (not currently mapped to InputRecord)

    Entity-type logic:
      'Individual'  when FIRST_NAME or LAST_NAME is non-null
      'Entity'      when all three name fields are null (ORG_NAME used as last_name)
    """
    top_clause   = f"TOP {top_rows}" if top_rows else ""
    where_clause = f"WHERE ScreeningInput_ID >= {start_row}" if start_row else ""

    print(f"Loading input from [{server}].[{database}].[{schema}].[{table}] ...")
    if start_row:
        print(f"  Starting at ScreeningInput_ID >= {start_row}")
    if top_rows:
        print(f"  Limiting to {top_rows} rows")

    with pyodbc.connect(_conn_str(server, database)) as conn:
        # PHONE column is optional — older tables may not have it yet.
        has_phone = conn.cursor().execute(
            "SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ? AND COLUMN_NAME = 'PHONE'",
            [schema, table]
        ).fetchone() is not None
        phone_col = "PHONE" if has_phone else "NULL AS PHONE"

        sql = f"""
            SELECT {top_clause} ScreeningInput_ID, ENTITY_NAME, ENTITY_NUM, ORG_NAME,
                   FIRST_NAME, MIDDLE_NAME, LAST_NAME,
                   ADDRESS1, ADDRESS2, ADDRESS3,
                   CITY, STATE, POSTAL_CODE, COUNTRY,
                   {phone_col}, Contact_ID, Entity_ID
            FROM   [{schema}].[{table}]
            {where_clause}
            ORDER BY ScreeningInput_ID
        """
        rows = conn.cursor().execute(sql).fetchall()

    recs = []
    for row in rows:
        screening_id = str(row[0])
        entity_name  = _s(row[1])
        # row[2] = ENTITY_NUM — available but external_id uses ScreeningInput_ID
        org_name     = _s(row[3])
        first_name   = _s(row[4])
        middle_name  = _s(row[5])
        last_name    = _s(row[6])
        address1     = _s(row[7])
        address2     = _s(row[8])
        address3     = _s(row[9])
        city         = _s(row[10])
        region       = _s(row[11])   # STATE column
        postal_code  = _s(row[12])
        country      = _s(row[13])
        phone        = _s(row[14])   # PHONE
        contact_id   = _s(row[15])   # Contact_ID
        entity_id    = _s(row[16])   # Entity_ID

        # Each ScreeningInput row produces up to two InputRecords so that name
        # evaluation is always performed at the correct granularity:
        #   Entity record   — ENTITY_NAME vs SDN org names (Pass 3/3b),
        #                     keyed on Entity_ID (falls back to ScreeningInput_ID)
        #   Individual record — FIRST/MIDDLE/LAST NAME vs SDN individual names
        #                       (Pass 1/2), keyed on Contact_ID (falls back to
        #                       ScreeningInput_ID).
        # Both records share the same address fields; Pass 4 deduplication cache
        # ensures address scoring runs only once for identical normalized addresses.

        _addr_kwargs = dict(
            entity_name = entity_name,
            address1    = address1,
            address2    = address2,
            address3    = address3,
            city        = city,
            region      = region,
            postal_code = postal_code,
            country     = country,
            phone       = phone,
        )

        # --- Entity evaluation: ENTITY_NAME ---
        eff_entity_name = entity_name or org_name
        if eff_entity_name:
            recs.append(InputRecord(
                entity_type = 'Entity',
                external_id = entity_id or screening_id,
                first_name  = None,
                middle_name = None,
                last_name   = None,
                **_addr_kwargs,
            ))

        # --- Individual evaluation: FIRST / MIDDLE / LAST NAME ---
        if first_name or middle_name or last_name:
            recs.append(InputRecord(
                entity_type = 'Individual',
                external_id = contact_id or screening_id,
                first_name  = first_name,
                middle_name = middle_name,
                last_name   = last_name,
                **_addr_kwargs,
            ))

    print(f"  {len(recs):,} records loaded.")
    return recs


# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------

_DETAIL_HEADER = [
    'run_id', 'input_id',
    'sdn_uid', 'sdn_source', 'sdn_field', 'sdn_full_text',
    'input_field', 'input_value',
    'comparison_level',
    'input_word_pos', 'sdn_word_pos', 'input_word', 'sdn_word',
    'method', 'is_match', 'match_score',
    'jaro_winkler_distance', 'jaro_winkler_similarity',
]

# ---------------------------------------------------------------------------
# MatchingResults_Person_Full  DDL + scoring + flush
# ---------------------------------------------------------------------------

_DDL_FULL = """
IF OBJECT_ID(N'[{s}].[MatchingResults_Person_Full]', N'U') IS NULL
BEGIN
    CREATE TABLE [{s}].[MatchingResults_Person_Full] (
        MatchingResults_ID                        BIGINT        NOT NULL IDENTITY(1,1),
        Run_ID                                    INT           NOT NULL,
        Input_Record_ID                           BIGINT        NOT NULL,
        SDN_Publish_Date                          DATE          NULL,
        SourceFN                                  VARCHAR(255)  NULL,   -- first name as submitted
        SourceMN                                  VARCHAR(255)  NULL,   -- middle name as submitted
        SourceLN                                  VARCHAR(255)  NULL,   -- last name as submitted
        SourceFN_NM                               VARCHAR(255)  NULL,   -- first name, punctuation removed
        SourceMN_NM                               VARCHAR(255)  NULL,   -- middle name, punctuation removed
        SourceLN_NM                               VARCHAR(255)  NULL,   -- last name, punctuation removed
        SDN_UID                                   INT           NULL,
        SDNFN                                     VARCHAR(255)  NULL,   -- SDN first name
        SDNLN                                     VARCHAR(900)  NULL,   -- SDN last name
        SDN_Type                                  VARCHAR(50)   NULL,
        SDN_AKA_Source                            VARCHAR(20)   NULL,
        FirstName_JaroWinklerSimilarity           DECIMAL(5,2)  NOT NULL CONSTRAINT DF_MRF_FN_JW DEFAULT 0,
        LastName_JaroWinklerSimilarity            DECIMAL(5,2)  NOT NULL CONSTRAINT DF_MRF_LN_JW DEFAULT 0,
        Personal_Name_Match                       BIT           NOT NULL CONSTRAINT DF_MRF_PNM   DEFAULT 0,
        CONSTRAINT PK_MatchingResults_Person_Full PRIMARY KEY (MatchingResults_ID),
        CONSTRAINT FK_MRF_RunLog FOREIGN KEY (Run_ID)
            REFERENCES [{s}].[MatchingResults_v2_RunLog] (run_id)
    );
    CREATE INDEX IX_MRF_Run      ON [{s}].[MatchingResults_Person_Full] (Run_ID);
    CREATE INDEX IX_MRF_SDN      ON [{s}].[MatchingResults_Person_Full] (SDN_UID);
    CREATE INDEX IX_MRF_LastName ON [{s}].[MatchingResults_Person_Full] (SourceLN_NM, SourceFN_NM);
END;
"""

_FULL_INSERT_SQL = """
INSERT INTO [{s}].[MatchingResults_Person_Full] (
    Run_ID, Input_Record_ID, SDN_Publish_Date,
    SourceFN, SourceMN, SourceLN, SourceFN_NM, SourceMN_NM, SourceLN_NM,
    SDN_UID, SDNFN, SDNLN, SDN_Type, SDN_AKA_Source,
    FirstName_JaroWinklerSimilarity,
    LastName_JaroWinklerSimilarity,
    Personal_Name_Match
) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
"""  # 17 placeholders

_DDL_NO_MATCH = """
IF OBJECT_ID(N'[{s}].[MatchingResults_Person_NoMatch]', N'U') IS NULL
BEGIN
    CREATE TABLE [{s}].[MatchingResults_Person_NoMatch] (
        NoMatch_ID                                BIGINT        NOT NULL IDENTITY(1,1),
        Run_ID                                    INT           NOT NULL,
        Input_Record_ID                           BIGINT        NOT NULL,
        SDN_UID                                   INT           NOT NULL,
        FirstName_JaroWinklerSimilarity           DECIMAL(5,2)  NOT NULL CONSTRAINT DF_MRNM_FN_JW DEFAULT 0,
        LastName_JaroWinklerSimilarity            DECIMAL(5,2)  NOT NULL CONSTRAINT DF_MRNM_LN_JW DEFAULT 0,
        CONSTRAINT PK_MatchingResults_Person_NoMatch PRIMARY KEY (NoMatch_ID),
        CONSTRAINT FK_MRNM_RunLog FOREIGN KEY (Run_ID)
            REFERENCES [{s}].[MatchingResults_v2_RunLog] (run_id)
    );
    CREATE INDEX IX_MRNM_Run         ON [{s}].[MatchingResults_Person_NoMatch] (Run_ID);
    CREATE INDEX IX_MRNM_InputRecord ON [{s}].[MatchingResults_Person_NoMatch] (Input_Record_ID);
    CREATE INDEX IX_MRNM_SDN         ON [{s}].[MatchingResults_Person_NoMatch] (SDN_UID);
END;
"""

_NO_MATCH_INSERT_SQL = """
INSERT INTO [{s}].[MatchingResults_Person_NoMatch] (
    Run_ID, Input_Record_ID, SDN_UID,
    FirstName_JaroWinklerSimilarity, LastName_JaroWinklerSimilarity
) VALUES (?,?,?,?,?)
"""  # 5 placeholders

# ---------------------------------------------------------------------------
# MatchingResults_AKA  DDL + flush
# One scored row per (input record × AKA entry) where the parent sdnEntry
# was a name candidate.  Identifier: SDN_UID + AKA_First_Name + AKA_Last_Name.
# ---------------------------------------------------------------------------

_DDL_AKA = """
IF OBJECT_ID(N'[{s}].[MatchingResults_AKA]', N'U') IS NULL
BEGIN
    CREATE TABLE [{s}].[MatchingResults_AKA] (
        AKA_Result_ID                             BIGINT        NOT NULL IDENTITY(1,1),
        Run_ID                                    INT           NOT NULL,
        Input_Record_ID                           BIGINT        NOT NULL,
        SDN_UID                                   INT           NOT NULL,
        AKA_UID                                   INT           NULL,
        AKA_Category                              VARCHAR(50)   NULL,
        SourceFN                                  VARCHAR(255)  NULL,   -- first name as submitted
        SourceMN                                  VARCHAR(255)  NULL,   -- middle name as submitted
        SourceLN                                  VARCHAR(255)  NULL,   -- last name as submitted
        SourceFN_NM                               VARCHAR(255)  NULL,   -- first name, punctuation removed
        SourceMN_NM                               VARCHAR(255)  NULL,   -- middle name, punctuation removed
        SourceLN_NM                               VARCHAR(255)  NULL,   -- last name, punctuation removed
        SDNFN                                     VARCHAR(255)  NULL,   -- AKA first name
        SDNLN                                     VARCHAR(900)  NULL,   -- AKA last name
        FirstName_JaroWinklerSimilarity           DECIMAL(5,2)  NOT NULL CONSTRAINT DF_MRAKA_FN_JW DEFAULT 0,
        LastName_JaroWinklerSimilarity            DECIMAL(5,2)  NOT NULL CONSTRAINT DF_MRAKA_LN_JW DEFAULT 0,
        Personal_Name_Match                       BIT           NOT NULL CONSTRAINT DF_MRAKA_PNM   DEFAULT 0,
        Recorded_At                               DATETIME      NOT NULL CONSTRAINT DF_MRAKA_RecordedAt DEFAULT GETDATE(),
        CONSTRAINT PK_MatchingResults_AKA PRIMARY KEY (AKA_Result_ID),
        CONSTRAINT FK_MRAKA_RunLog FOREIGN KEY (Run_ID)
            REFERENCES [{s}].[MatchingResults_v2_RunLog] (run_id)
    );
    CREATE INDEX IX_MRAKA_Run         ON [{s}].[MatchingResults_AKA] (Run_ID);
    CREATE INDEX IX_MRAKA_SDN         ON [{s}].[MatchingResults_AKA] (SDN_UID);
    CREATE INDEX IX_MRAKA_InputRecord ON [{s}].[MatchingResults_AKA] (Input_Record_ID);
END;
"""

_AKA_INSERT_SQL = """
INSERT INTO [{s}].[MatchingResults_AKA] (
    Run_ID, Input_Record_ID, SDN_UID, AKA_UID, AKA_Category,
    SourceFN, SourceMN, SourceLN, SourceFN_NM, SourceMN_NM, SourceLN_NM, SDNFN, SDNLN,
    FirstName_JaroWinklerSimilarity,
    LastName_JaroWinklerSimilarity,
    Personal_Name_Match
) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
"""  # 16 placeholders

# ---------------------------------------------------------------------------
# MatchingResults_AKA_NoMatch  DDL + flush
# Slim row per (input record × AKA entry) where the parent sdnEntry was NOT
# a candidate.  Identifier: SDN_UID + AKA_First_Name + AKA_Last_Name.
# ---------------------------------------------------------------------------

_DDL_AKA_NO_MATCH = """
IF OBJECT_ID(N'[{s}].[MatchingResults_AKA_NoMatch]', N'U') IS NULL
BEGIN
    CREATE TABLE [{s}].[MatchingResults_AKA_NoMatch] (
        NoMatch_ID                                BIGINT        NOT NULL IDENTITY(1,1),
        Run_ID                                    INT           NOT NULL,
        Input_Record_ID                           BIGINT        NOT NULL,
        SDN_UID                                   INT           NOT NULL,
        AKA_First_Name                            VARCHAR(255)  NULL,
        AKA_Last_Name                             VARCHAR(900)  NULL,
        FirstName_JaroWinklerSimilarity           DECIMAL(5,2)  NOT NULL CONSTRAINT DF_MRAKANM_FN_JW DEFAULT 0,
        LastName_JaroWinklerSimilarity            DECIMAL(5,2)  NOT NULL CONSTRAINT DF_MRAKANM_LN_JW DEFAULT 0,
        CONSTRAINT PK_MatchingResults_AKA_NoMatch PRIMARY KEY (NoMatch_ID),
        CONSTRAINT FK_MRAKANM_RunLog FOREIGN KEY (Run_ID)
            REFERENCES [{s}].[MatchingResults_v2_RunLog] (run_id)
    );
    CREATE INDEX IX_MRAKANM_Run         ON [{s}].[MatchingResults_AKA_NoMatch] (Run_ID);
    CREATE INDEX IX_MRAKANM_InputRecord ON [{s}].[MatchingResults_AKA_NoMatch] (Input_Record_ID);
    CREATE INDEX IX_MRAKANM_SDN         ON [{s}].[MatchingResults_AKA_NoMatch] (SDN_UID);
END;
"""

_AKA_NO_MATCH_INSERT_SQL = """
INSERT INTO [{s}].[MatchingResults_AKA_NoMatch] (
    Run_ID, Input_Record_ID, SDN_UID, AKA_First_Name, AKA_Last_Name,
    FirstName_JaroWinklerSimilarity, LastName_JaroWinklerSimilarity
) VALUES (?,?,?,?,?,?,?)
"""  # 7 placeholders

# ---------------------------------------------------------------------------
# MatchingResults_OrgName  DDL + flush
# One row per (input record × Entity sdnEntry) that shared at least one
# org-name word (candidate pre-filter).  Scores are word-match counts.
# ---------------------------------------------------------------------------

_DDL_ORG = """
IF OBJECT_ID(N'[{s}].[MatchingResults_OrgName]', N'U') IS NULL
BEGIN
    CREATE TABLE [{s}].[MatchingResults_OrgName] (
        OrgName_Result_ID          BIGINT        NOT NULL IDENTITY(1,1),
        Run_ID                     INT           NOT NULL,
        Input_Record_ID            BIGINT        NOT NULL,
        SDN_Publish_Date           DATE          NULL,
        SourceOrgName              VARCHAR(900)  NULL,   -- org name as submitted
        SourceOrgName_NM           VARCHAR(900)  NULL,   -- org name, punctuation removed
        SDN_UID                    INT           NOT NULL,
        SDNOrgName                 VARCHAR(900)  NULL,   -- SDN entity name (raw)
        SDNOrgName_NM              VARCHAR(900)  NULL,   -- SDN entity name, punctuation removed
        SDN_Type                   VARCHAR(50)   NULL,
        SDN_AKA_Source             VARCHAR(20)   NULL,
        SourceNumberofWords            INT          NOT NULL CONSTRAINT DF_MRORG_SrcWC  DEFAULT 0,
        SDNNumberofWords               INT          NOT NULL CONSTRAINT DF_MRORG_SdnWC  DEFAULT 0,
        WordNumberMatchingJaroWinkler  INT          NOT NULL CONSTRAINT DF_MRORG_JW     DEFAULT 0,
        FullName_JaroWinklerSimilarity DECIMAL(5,2) NOT NULL CONSTRAINT DF_MRORG_FNJW   DEFAULT 0,
        CONSTRAINT PK_MatchingResults_OrgName PRIMARY KEY (OrgName_Result_ID),
        CONSTRAINT FK_MRORG_RunLog FOREIGN KEY (Run_ID)
            REFERENCES [{s}].[MatchingResults_v2_RunLog] (run_id)
    );
    CREATE INDEX IX_MRORG_Run         ON [{s}].[MatchingResults_OrgName] (Run_ID);
    CREATE INDEX IX_MRORG_SDN         ON [{s}].[MatchingResults_OrgName] (SDN_UID);
    CREATE INDEX IX_MRORG_InputRecord ON [{s}].[MatchingResults_OrgName] (Input_Record_ID);
END;
"""

_ORG_INSERT_SQL = """
INSERT INTO [{s}].[MatchingResults_OrgName] (
    Run_ID, Input_Record_ID, SDN_Publish_Date,
    SourceOrgName, SourceOrgName_NM,
    SDN_UID, SDNOrgName, SDNOrgName_NM, SDN_Type, SDN_AKA_Source,
    SourceNumberofWords, SDNNumberofWords,
    WordNumberMatchingJaroWinkler,
    FullName_JaroWinklerSimilarity
) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
"""

# ---------------------------------------------------------------------------
# MatchingResults_OrgName_NoMatch  DDL + flush
# Slim row per (input record × Entity sdnEntry) with no shared org-name word.
# ---------------------------------------------------------------------------

_DDL_ORG_NO_MATCH = """
IF OBJECT_ID(N'[{s}].[MatchingResults_OrgName_NoMatch]', N'U') IS NULL
BEGIN
    CREATE TABLE [{s}].[MatchingResults_OrgName_NoMatch] (
        NoMatch_ID                     BIGINT        NOT NULL IDENTITY(1,1),
        Run_ID                         INT           NOT NULL,
        Input_Record_ID                BIGINT        NOT NULL,
        SDN_UID                        INT           NOT NULL,
        FullName_JaroWinklerSimilarity DECIMAL(5,2)  NOT NULL CONSTRAINT DF_MRORGDNM_JW DEFAULT 0,
        CONSTRAINT PK_MatchingResults_OrgName_NoMatch PRIMARY KEY (NoMatch_ID),
        CONSTRAINT FK_MRORGDNM_RunLog FOREIGN KEY (Run_ID)
            REFERENCES [{s}].[MatchingResults_v2_RunLog] (run_id)
    );
    CREATE INDEX IX_MRORGDNM_Run         ON [{s}].[MatchingResults_OrgName_NoMatch] (Run_ID);
    CREATE INDEX IX_MRORGDNM_InputRecord ON [{s}].[MatchingResults_OrgName_NoMatch] (Input_Record_ID);
    CREATE INDEX IX_MRORGDNM_SDN         ON [{s}].[MatchingResults_OrgName_NoMatch] (SDN_UID);
END;
"""

_ORG_NO_MATCH_INSERT_SQL = """
INSERT INTO [{s}].[MatchingResults_OrgName_NoMatch]
    (Run_ID, Input_Record_ID, SDN_UID, FullName_JaroWinklerSimilarity)
VALUES (?,?,?,?)
"""  # 4 placeholders

# ---------------------------------------------------------------------------
# MatchingResults_OrgName_AKA  DDL + flush
# One row per (input record × Entity AKA entry) that shared at least one
# org-name word with the AKA name.  Scores are word-match counts.
# ---------------------------------------------------------------------------

_DDL_ORG_AKA = """
IF OBJECT_ID(N'[{s}].[MatchingResults_OrgName_AKA]', N'U') IS NULL
BEGIN
    CREATE TABLE [{s}].[MatchingResults_OrgName_AKA] (
        OrgName_AKA_Result_ID      BIGINT        NOT NULL IDENTITY(1,1),
        Run_ID                     INT           NOT NULL,
        Input_Record_ID            BIGINT        NOT NULL,
        SDN_Publish_Date           DATE          NULL,
        SourceOrgName              VARCHAR(900)  NULL,   -- org name as submitted
        SourceOrgName_NM           VARCHAR(900)  NULL,   -- org name, punctuation removed
        SDN_UID                    INT           NOT NULL,
        AKA_UID                    INT           NULL,
        AKA_Category               VARCHAR(50)   NULL,
        SDNOrgName                 VARCHAR(900)  NULL,   -- AKA entity name (raw)
        SDNOrgName_NM              VARCHAR(900)  NULL,   -- AKA entity name, punctuation removed
        SDN_Type                   VARCHAR(50)   NULL,
        SDN_AKA_Source             VARCHAR(20)   NULL,
        SourceNumberofWords            INT          NOT NULL CONSTRAINT DF_MRORGAKA_SrcWC  DEFAULT 0,
        SDNNumberofWords               INT          NOT NULL CONSTRAINT DF_MRORGAKA_SdnWC  DEFAULT 0,
        WordNumberMatchingJaroWinkler  INT          NOT NULL CONSTRAINT DF_MRORGAKA_JW     DEFAULT 0,
        FullName_JaroWinklerSimilarity DECIMAL(5,2) NOT NULL CONSTRAINT DF_MRORGAKA_FNJW   DEFAULT 0,
        CONSTRAINT PK_MatchingResults_OrgName_AKA PRIMARY KEY (OrgName_AKA_Result_ID),
        CONSTRAINT FK_MRORGAKA_RunLog FOREIGN KEY (Run_ID)
            REFERENCES [{s}].[MatchingResults_v2_RunLog] (run_id)
    );
    CREATE INDEX IX_MRORGAKA_Run         ON [{s}].[MatchingResults_OrgName_AKA] (Run_ID);
    CREATE INDEX IX_MRORGAKA_SDN         ON [{s}].[MatchingResults_OrgName_AKA] (SDN_UID);
    CREATE INDEX IX_MRORGAKA_InputRecord ON [{s}].[MatchingResults_OrgName_AKA] (Input_Record_ID);
END;
"""

_ORG_AKA_INSERT_SQL = """
INSERT INTO [{s}].[MatchingResults_OrgName_AKA] (
    Run_ID, Input_Record_ID, SDN_Publish_Date,
    SourceOrgName, SourceOrgName_NM,
    SDN_UID, AKA_UID, AKA_Category,
    SDNOrgName, SDNOrgName_NM, SDN_Type, SDN_AKA_Source,
    SourceNumberofWords, SDNNumberofWords,
    WordNumberMatchingJaroWinkler,
    FullName_JaroWinklerSimilarity
) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
"""

# ---------------------------------------------------------------------------
# MatchingResults_OrgName_AKA_NoMatch  DDL + flush
# Slim row per (input record × Entity AKA entry) with no shared org-name word.
# ---------------------------------------------------------------------------

_DDL_ORG_AKA_NO_MATCH = """
IF OBJECT_ID(N'[{s}].[MatchingResults_OrgName_AKA_NoMatch]', N'U') IS NULL
BEGIN
    CREATE TABLE [{s}].[MatchingResults_OrgName_AKA_NoMatch] (
        NoMatch_ID                     BIGINT        NOT NULL IDENTITY(1,1),
        Run_ID                         INT           NOT NULL,
        Input_Record_ID                BIGINT        NOT NULL,
        SDN_UID                        INT           NOT NULL,
        SDNOrgName                     VARCHAR(900)  NULL,
        FullName_JaroWinklerSimilarity DECIMAL(5,2)  NOT NULL CONSTRAINT DF_MRORGAKANM_JW DEFAULT 0,
        CONSTRAINT PK_MatchingResults_OrgName_AKA_NoMatch PRIMARY KEY (NoMatch_ID),
        CONSTRAINT FK_MRORGAKANM_RunLog FOREIGN KEY (Run_ID)
            REFERENCES [{s}].[MatchingResults_v2_RunLog] (run_id)
    );
    CREATE INDEX IX_MRORGAKANM_Run         ON [{s}].[MatchingResults_OrgName_AKA_NoMatch] (Run_ID);
    CREATE INDEX IX_MRORGAKANM_InputRecord ON [{s}].[MatchingResults_OrgName_AKA_NoMatch] (Input_Record_ID);
    CREATE INDEX IX_MRORGAKANM_SDN         ON [{s}].[MatchingResults_OrgName_AKA_NoMatch] (SDN_UID);
END;
"""

_ORG_AKA_NO_MATCH_INSERT_SQL = """
INSERT INTO [{s}].[MatchingResults_OrgName_AKA_NoMatch]
    (Run_ID, Input_Record_ID, SDN_UID, SDNOrgName, FullName_JaroWinklerSimilarity)
VALUES (?,?,?,?,?)
"""  # 5 placeholders


# ---------------------------------------------------------------------------
# StreetAddressMatchType  DDL  (static 32-row lookup table)
# ---------------------------------------------------------------------------

_DDL_ADDR_MATCH_TYPE = """
IF OBJECT_ID(N'[{s}].[StreetAddressMatchType]', N'U') IS NULL
CREATE TABLE [{s}].[StreetAddressMatchType] (
    ID                     SMALLINT     NOT NULL,
    Method                 VARCHAR(20)  NOT NULL,  -- Direct | JaroWinkler
    StreetAddressMatchType VARCHAR(100) NOT NULL,
    CONSTRAINT PK_SAMT PRIMARY KEY (ID)
);
"""

# ---------------------------------------------------------------------------
# MatchingResults_Address  DDL + flush
# One row per (input record × candidate SDN address record).
# "Candidate" = at least one normalized word (len > 2) shared between
# the input mailing fields and the SDN address/city/country fields.
# Non-candidates are silently skipped (no separate NoMatch table).
# ---------------------------------------------------------------------------

_DDL_ADDR_FULL = """
IF OBJECT_ID(N'[{s}].[MatchingResults_Address]', N'U') IS NULL
BEGIN
    CREATE TABLE [{s}].[MatchingResults_Address] (
        Address_Result_ID                   BIGINT        NOT NULL IDENTITY(1,1),
        Run_ID                              INT           NOT NULL,
        Input_Record_ID                     BIGINT        NOT NULL,
        SDNEntry_UID                        INT           NOT NULL,
        SDNAddress_UID                      INT           NOT NULL,
        Source_UID                          VARCHAR(255)  NULL,   -- external source identifier
        -- Source (input) address fields, raw + normalized
        SourceAddress1                      VARCHAR(500)  NULL,
        SourceAddress1_NM                   VARCHAR(500)  NULL,
        SourceAddress2                      VARCHAR(500)  NULL,
        SourceAddress2_NM                   VARCHAR(500)  NULL,
        SourceAddress3                      VARCHAR(500)  NULL,
        SourceAddress3_NM                   VARCHAR(500)  NULL,
        SourceCity                          VARCHAR(200)  NULL,
        SourceCity_NM                       VARCHAR(200)  NULL,
        SourceStateProvince                 VARCHAR(100)  NULL,
        SourceStateProvince_NM              VARCHAR(100)  NULL,
        SourcePostalCode                    VARCHAR(20)   NULL,
        SourcePostalCode_NM                 VARCHAR(20)   NULL,
        SourceCountry                       VARCHAR(100)  NULL,
        SourceCountry_NM                    VARCHAR(100)  NULL,
        -- SDN address fields, raw + normalized
        SDNAddress1                         VARCHAR(500)  NULL,
        SDNAddress1_NM                      VARCHAR(500)  NULL,
        SDNAddress2                         VARCHAR(500)  NULL,
        SDNAddress2_NM                      VARCHAR(500)  NULL,
        SDNAddress3                         VARCHAR(500)  NULL,
        SDNAddress3_NM                      VARCHAR(500)  NULL,
        SDNCity                             VARCHAR(200)  NULL,
        SDNCity_NM                          VARCHAR(200)  NULL,
        SDNStateProvince                    VARCHAR(100)  NULL,
        SDNStateProvince_NM                 VARCHAR(100)  NULL,
        SDNPostalCode                       VARCHAR(20)   NULL,
        SDNPostalCode_NM                    VARCHAR(20)   NULL,
        SDNCountry                          VARCHAR(100)  NULL,
        SDNCountry_NM                       VARCHAR(100)  NULL,
        -- Street address match type (FK to lookup; 32 = No Match)
        JaroWinklerMatchStreetAddress       SMALLINT      NOT NULL CONSTRAINT DF_MRA_JW DEFAULT 32,
        -- Geo-field scores (JW similarity %)
        City_JaroWinklerSimilarity          DECIMAL(5,2)  NOT NULL CONSTRAINT DF_MRA_CityJW  DEFAULT 0,
        StateProvince_JaroWinklerSimilarity DECIMAL(5,2)  NOT NULL CONSTRAINT DF_MRA_StJW    DEFAULT 0,
        PostalCode_JaroWinklerSimilarity    DECIMAL(5,2)  NOT NULL CONSTRAINT DF_MRA_PcJW    DEFAULT 0,
        Country_JaroWinklerSimilarity       DECIMAL(5,2)  NOT NULL CONSTRAINT DF_MRA_CoJW    DEFAULT 0,
        CONSTRAINT PK_MatchingResults_Address PRIMARY KEY (Address_Result_ID),
        CONSTRAINT FK_MRA_RunLog      FOREIGN KEY (Run_ID)
            REFERENCES [{s}].[MatchingResults_v2_RunLog] (run_id),
        CONSTRAINT FK_MRA_JWMatchType FOREIGN KEY (JaroWinklerMatchStreetAddress)
            REFERENCES [{s}].[StreetAddressMatchType] (ID)
    );
    CREATE INDEX IX_MRA_Run         ON [{s}].[MatchingResults_Address] (Run_ID);
    CREATE INDEX IX_MRA_SDNEntry    ON [{s}].[MatchingResults_Address] (SDNEntry_UID);
    CREATE INDEX IX_MRA_InputRecord ON [{s}].[MatchingResults_Address] (Input_Record_ID);
END;
"""

# ---------------------------------------------------------------------------
# MatchingResults_Address_NoMatch  DDL + flush
# One slim row per (input record × SDN address record) where no address
# component (excluding State/Region) met the JW threshold.
# ---------------------------------------------------------------------------

_DDL_ADDR_NO_MATCH = """
IF OBJECT_ID(N'[{s}].[MatchingResults_Address_NoMatch]', N'U') IS NULL
BEGIN
    CREATE TABLE [{s}].[MatchingResults_Address_NoMatch] (
        Address_NoMatch_ID  BIGINT   NOT NULL IDENTITY(1,1),
        Run_ID              INT      NOT NULL,
        Input_Record_ID     BIGINT   NOT NULL,
        SDNEntry_UID        INT      NOT NULL,
        SDNAddress_UID      INT      NOT NULL,
        CONSTRAINT PK_MatchingResults_Address_NoMatch PRIMARY KEY (Address_NoMatch_ID),
        CONSTRAINT FK_MRANOM_RunLog FOREIGN KEY (Run_ID)
            REFERENCES [{s}].[MatchingResults_v2_RunLog] (run_id)
    );
    CREATE INDEX IX_MRANOM_Run         ON [{s}].[MatchingResults_Address_NoMatch] (Run_ID);
    CREATE INDEX IX_MRANOM_SDNEntry    ON [{s}].[MatchingResults_Address_NoMatch] (SDNEntry_UID);
    CREATE INDEX IX_MRANOM_InputRecord ON [{s}].[MatchingResults_Address_NoMatch] (Input_Record_ID);
END;
"""

_ADDR_NO_MATCH_INSERT_SQL = """
INSERT INTO [{s}].[MatchingResults_Address_NoMatch]
    (Run_ID, Input_Record_ID, SDNEntry_UID, SDNAddress_UID)
VALUES (?,?,?,?)
"""  # 4 placeholders

_ADDR_FULL_INSERT_SQL = """
INSERT INTO [{s}].[MatchingResults_Address] (
    Run_ID, Input_Record_ID, SDNEntry_UID, SDNAddress_UID, Source_UID,
    SourceAddress1, SourceAddress1_NM, SourceAddress2, SourceAddress2_NM,
    SourceAddress3, SourceAddress3_NM,
    SourceCity, SourceCity_NM, SourceStateProvince, SourceStateProvince_NM,
    SourcePostalCode, SourcePostalCode_NM, SourceCountry, SourceCountry_NM,
    SDNAddress1, SDNAddress1_NM, SDNAddress2, SDNAddress2_NM,
    SDNAddress3, SDNAddress3_NM,
    SDNCity, SDNCity_NM, SDNStateProvince, SDNStateProvince_NM,
    SDNPostalCode, SDNPostalCode_NM, SDNCountry, SDNCountry_NM,
    JaroWinklerMatchStreetAddress,
    City_JaroWinklerSimilarity,
    StateProvince_JaroWinklerSimilarity,
    PostalCode_JaroWinklerSimilarity,
    Country_JaroWinklerSimilarity
) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
"""  # 38 placeholders


# ---------------------------------------------------------------------------
# MatchingResults_LinkedTo  DDL + flush
# One row per (input record × SDN Linked-to occurrence) where the input name
# shares at least one word (len > 2) with the Linked-to text.
# ---------------------------------------------------------------------------

_DDL_LINKED_TO = """
IF OBJECT_ID(N'[{s}].[MatchingResults_LinkedTo]', N'U') IS NULL
BEGIN
    CREATE TABLE [{s}].[MatchingResults_LinkedTo] (
        LinkedTo_Result_ID              BIGINT        NOT NULL IDENTITY(1,1),
        Run_ID                          INT           NOT NULL,
        Input_Record_ID                 BIGINT        NOT NULL,
        SDN_UID                         INT           NOT NULL,
        SDN_Publish_Date                DATE          NULL,
        LinkedTo_Occurrence             SMALLINT      NOT NULL,
        LinkedTo_Text                   VARCHAR(500)  NULL,   -- raw text after "Linked to:"
        LinkedTo_Text_NM                VARCHAR(500)  NULL,   -- lowercased, whitespace-collapsed
        LinkedTo_Text_NM_Expanded       VARCHAR(500)  NULL,   -- entity-suffix-expanded, normalized (uppercase)
        SourceName                      VARCHAR(500)  NULL,   -- input name as submitted
        SourceName_NM                   VARCHAR(500)  NULL,   -- normalized input name
        Input_Name_Form                 VARCHAR(20)   NULL,   -- FN_MN_LN | LN_FN_MN | EntityName
        SourceNumberOfWords             INT           NOT NULL CONSTRAINT DF_MRLT_SrcWC DEFAULT 0,
        LinkedToNumberOfWords           INT           NOT NULL CONSTRAINT DF_MRLT_LtWC  DEFAULT 0,
        WordNumberMatchingJaroWinkler   INT           NOT NULL CONSTRAINT DF_MRLT_JWW   DEFAULT 0,
        FullName_JaroWinklerSimilarity  DECIMAL(5,2)  NOT NULL CONSTRAINT DF_MRLT_JW    DEFAULT 0,
        CONSTRAINT PK_MatchingResults_LinkedTo PRIMARY KEY (LinkedTo_Result_ID),
        CONSTRAINT FK_MRLT_RunLog FOREIGN KEY (Run_ID)
            REFERENCES [{s}].[MatchingResults_v2_RunLog] (run_id)
    );
    CREATE INDEX IX_MRLT_Run         ON [{s}].[MatchingResults_LinkedTo] (Run_ID);
    CREATE INDEX IX_MRLT_SDN         ON [{s}].[MatchingResults_LinkedTo] (SDN_UID);
    CREATE INDEX IX_MRLT_InputRecord ON [{s}].[MatchingResults_LinkedTo] (Input_Record_ID);
END;
"""

_LINKED_TO_INSERT_SQL = """
INSERT INTO [{s}].[MatchingResults_LinkedTo] (
    Run_ID, Input_Record_ID, SDN_UID, SDN_Publish_Date, LinkedTo_Occurrence,
    LinkedTo_Text, LinkedTo_Text_NM, LinkedTo_Text_NM_Expanded, SourceName, SourceName_NM, Input_Name_Form,
    SourceNumberOfWords, LinkedToNumberOfWords, WordNumberMatchingJaroWinkler,
    FullName_JaroWinklerSimilarity
) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
"""  # 15 placeholders


# ---------------------------------------------------------------------------
# MatchingResults_LinkedTo_NoMatch  DDL + flush
# One slim row per (input record × SDN UID with Linked-to text) where the
# input name shared no word (len > 2) with any of that UID's Linked-to clauses.
# Paired with MatchingResults_LinkedTo to give full coverage proof.
# ---------------------------------------------------------------------------

_DDL_LINKED_TO_NO_MATCH = """
IF OBJECT_ID(N'[{s}].[MatchingResults_LinkedTo_NoMatch]', N'U') IS NULL
BEGIN
    CREATE TABLE [{s}].[MatchingResults_LinkedTo_NoMatch] (
        LinkedTo_NoMatch_ID            BIGINT        NOT NULL IDENTITY(1,1),
        Run_ID                         INT           NOT NULL,
        Input_Record_ID                BIGINT        NOT NULL,
        SDN_UID                        INT           NOT NULL,
        FullName_JaroWinklerSimilarity DECIMAL(5,2)  NOT NULL CONSTRAINT DF_MRLTNOM_JW DEFAULT 0,
        CONSTRAINT PK_MatchingResults_LinkedTo_NoMatch PRIMARY KEY (LinkedTo_NoMatch_ID),
        CONSTRAINT FK_MRLTNOM_RunLog FOREIGN KEY (Run_ID)
            REFERENCES [{s}].[MatchingResults_v2_RunLog] (run_id)
    );
    CREATE INDEX IX_MRLTNOM_Run         ON [{s}].[MatchingResults_LinkedTo_NoMatch] (Run_ID);
    CREATE INDEX IX_MRLTNOM_SDN         ON [{s}].[MatchingResults_LinkedTo_NoMatch] (SDN_UID);
    CREATE INDEX IX_MRLTNOM_InputRecord ON [{s}].[MatchingResults_LinkedTo_NoMatch] (Input_Record_ID);
END;
"""

_LINKED_TO_NO_MATCH_INSERT_SQL = """
INSERT INTO [{s}].[MatchingResults_LinkedTo_NoMatch]
    (Run_ID, Input_Record_ID, SDN_UID, FullName_JaroWinklerSimilarity)
VALUES (?,?,?,?)
"""  # 4 placeholders


# ---------------------------------------------------------------------------
# MatchingResults_Phone  DDL + flush
# One row per (input record × SDN phone number) where the input phone and
# the SDN phone share at least the last 7 digits.
# ---------------------------------------------------------------------------

_DDL_PHONE = """
IF OBJECT_ID(N'[{s}].[MatchingResults_Phone]', N'U') IS NULL
BEGIN
    CREATE TABLE [{s}].[MatchingResults_Phone] (
        Phone_Result_ID    BIGINT        NOT NULL IDENTITY(1,1),
        Run_ID             INT           NOT NULL,
        Input_Record_ID    BIGINT        NOT NULL,
        SDN_UID            INT           NOT NULL,
        SDN_Publish_Date   DATE          NULL,
        SDN_Phone_Raw      VARCHAR(100)  NULL,   -- raw phone string from remarks
        SDN_Phone_Digits   VARCHAR(50)   NULL,   -- digits only
        Input_Phone_Raw    VARCHAR(50)   NULL,   -- raw phone as submitted
        Input_Phone_Digits VARCHAR(50)   NULL,   -- digits only
        Exact_Match        BIT           NOT NULL CONSTRAINT DF_MRP_Exact   DEFAULT 0,
        Last10_Match       BIT           NOT NULL CONSTRAINT DF_MRP_Last10  DEFAULT 0,
        JaroWinkler_Digits DECIMAL(5,2)  NOT NULL CONSTRAINT DF_MRP_JW     DEFAULT 0,
        CONSTRAINT PK_MatchingResults_Phone PRIMARY KEY (Phone_Result_ID),
        CONSTRAINT FK_MRP_RunLog FOREIGN KEY (Run_ID)
            REFERENCES [{s}].[MatchingResults_v2_RunLog] (run_id)
    );
    CREATE INDEX IX_MRP_Run         ON [{s}].[MatchingResults_Phone] (Run_ID);
    CREATE INDEX IX_MRP_SDN         ON [{s}].[MatchingResults_Phone] (SDN_UID);
    CREATE INDEX IX_MRP_InputRecord ON [{s}].[MatchingResults_Phone] (Input_Record_ID);
END;
"""

_PHONE_INSERT_SQL = """
INSERT INTO [{s}].[MatchingResults_Phone] (
    Run_ID, Input_Record_ID, SDN_UID, SDN_Publish_Date,
    SDN_Phone_Raw, SDN_Phone_Digits, Input_Phone_Raw, Input_Phone_Digits,
    Exact_Match, Last10_Match, JaroWinkler_Digits
) VALUES (?,?,?,?,?,?,?,?,?,?,?)
"""  # 11 placeholders


# ---------------------------------------------------------------------------
# MatchingResults_NoMatch  — one row per input record with no match anywhere.
# Replaces the six per-pair NoMatch tables (Person, AKA, OrgName, OrgName_AKA,
# Address, LinkedTo) with a single lightweight log.
# ---------------------------------------------------------------------------

_DDL_NO_MATCH_LOG = """
IF OBJECT_ID(N'[{s}].[MatchingResults_NoMatch]', N'U') IS NULL
BEGIN
    CREATE TABLE [{s}].[MatchingResults_NoMatch] (
        NoMatch_ID       BIGINT        NOT NULL IDENTITY(1,1),
        Run_ID           INT           NOT NULL,
        Input_Record_ID  BIGINT        NOT NULL,
        SourceFN         VARCHAR(255)  NULL,
        SourceMN         VARCHAR(255)  NULL,
        SourceLN         VARCHAR(255)  NULL,
        SourceEntityName VARCHAR(500)  NULL,
        ExternalID       VARCHAR(255)  NULL,
        Note             VARCHAR(100)  NOT NULL
                             CONSTRAINT DF_MRNoMatch_Note DEFAULT 'No Match Found',
        Recorded_At      DATETIME      NOT NULL
                             CONSTRAINT DF_MRNoMatch_Recorded DEFAULT GETDATE(),
        CONSTRAINT PK_MatchingResults_NoMatch PRIMARY KEY (NoMatch_ID),
        CONSTRAINT FK_MRNoMatch_RunLog FOREIGN KEY (Run_ID)
            REFERENCES [{s}].[MatchingResults_v2_RunLog] (run_id)
    );
    CREATE INDEX IX_MRNoMatch_Run         ON [{s}].[MatchingResults_NoMatch] (Run_ID);
    CREATE INDEX IX_MRNoMatch_InputRecord ON [{s}].[MatchingResults_NoMatch] (Input_Record_ID);
END;
"""

_NO_MATCH_LOG_INSERT_SQL = """
INSERT INTO [{s}].[MatchingResults_NoMatch]
    (Run_ID, Input_Record_ID, SourceFN, SourceMN, SourceLN,
     SourceEntityName, ExternalID, Note)
VALUES (?,?,?,?,?,?,?,?)
"""  # 8 placeholders


def flush_no_match_log(conn, schema: str, rows: list, batch_size: int = 2000):
    """Write one 'No Match Found' row per unmatched input record."""
    if not rows:
        return
    sql = _NO_MATCH_LOG_INSERT_SQL.replace('{s}', schema)
    cur = conn.cursor()
    cur.fast_executemany = True
    for i in range(0, len(rows), batch_size):
        cur.executemany(sql, rows[i:i + batch_size])
    conn.commit()


# ---------------------------------------------------------------------------
# Matching_Summary_Person / Matching_Summary_Org
# Post-processing summary tables, populated AFTER all MatchingResults_* tables
# have been flushed for this run.  One row per (Input_ID, SDN_UID) combination
# that matched in any pass, plus one all-zero/NULL row per Input_ID that has
# no match anywhere (from MatchingResults_NoMatch).
#
# "Country_City match" = a row exists in MatchingResults_Address for the same
# (Input_Record_ID, SDN_UID) where City_JaroWinklerSimilarity >= 85 AND
# Country_JaroWinklerSimilarity >= 85.
# ---------------------------------------------------------------------------

_COUNTRY_CITY_JW_THRESHOLD = 85

_DDL_SUMMARY_PERSON = """
IF OBJECT_ID(N'[{s}].[Matching_Summary_Person]', N'U') IS NULL
BEGIN
    CREATE TABLE [{s}].[Matching_Summary_Person] (
        Summary_ID                                      BIGINT        NOT NULL IDENTITY(1,1),
        Run_ID                                          INT           NOT NULL,
        Input_ID                                        BIGINT        NOT NULL,
        SDN_UID                                         INT           NULL,
        Person_Regular_Match                            BIT           NOT NULL CONSTRAINT DF_MSP_PRM  DEFAULT 0,
        Input_Person_Name                               VARCHAR(255)  NULL,
        SDN_Person_Name                                 VARCHAR(255)  NULL,
        SDN_Person_Regular_Match                        VARCHAR(255)  NULL,
        Person_Regular_Match_FN_JW                      DECIMAL(5,2)  NULL,
        Person_Regular_Match_LN_JW                      DECIMAL(5,2)  NULL,
        Person_aka_Match                                BIT           NOT NULL CONSTRAINT DF_MSP_PAM  DEFAULT 0,
        Input_Person_aka_Name                           VARCHAR(255)  NULL,
        SDN_Person_aka_Name                             VARCHAR(255)  NULL,
        SDN_Person_aka_Match                            VARCHAR(255)  NULL,
        Person_aka_Match_FN_JW                          DECIMAL(5,2)  NULL,
        Person_aka_Match_LN_JW                          DECIMAL(5,2)  NULL,
        Person_LinkedTo_Match                           BIT           NOT NULL CONSTRAINT DF_MSP_PLM  DEFAULT 0,
        Input_Person_LinkedTo_Name                      VARCHAR(255)  NULL,
        SDN_Person_LinkedTo_Name                        VARCHAR(255)  NULL,
        SDN_Person_LinkedTo_Match                       VARCHAR(255)  NULL,
        Person_LinkedTo_Match_JW                        DECIMAL(5,2)  NULL,
        Country_City_Match_Person                       BIT           NOT NULL CONSTRAINT DF_MSP_CCP  DEFAULT 0,
        Input_City_Person                               VARCHAR(255)  NULL,
        SDN_City_Person                                 VARCHAR(255)  NULL,
        Input_Country_Person                            VARCHAR(255)  NULL,
        SDN_Country_Person                              VARCHAR(255)  NULL,
        Country_City_Match_Person_City_JW               DECIMAL(5,2)  NULL,
        Country_City_Match_Person_Country_JW            DECIMAL(5,2)  NULL,
        Country_City_Match_Person_aka                   BIT           NOT NULL CONSTRAINT DF_MSP_CCPA DEFAULT 0,
        Input_City_Person_aka                           VARCHAR(255)  NULL,
        SDN_City_Person_aka                             VARCHAR(255)  NULL,
        Input_Country_Person_aka                        VARCHAR(255)  NULL,
        SDN_Country_Person_aka                          VARCHAR(255)  NULL,
        Country_City_Match_Person_aka_City_JW           DECIMAL(5,2)  NULL,
        Country_City_Match_Person_aka_Country_JW        DECIMAL(5,2)  NULL,
        Country_City_Match_Person_Linked_To             BIT           NOT NULL CONSTRAINT DF_MSP_CCPL DEFAULT 0,
        Input_City_Person_Linked_To                     VARCHAR(255)  NULL,
        SDN_City_Person_Linked_To                       VARCHAR(255)  NULL,
        Input_Country_Person_Linked_To                  VARCHAR(255)  NULL,
        SDN_Country_Person_Linked_To                    VARCHAR(255)  NULL,
        Country_City_Match_Person_Linked_To_City_JW     DECIMAL(5,2)  NULL,
        Country_City_Match_Person_Linked_To_Country_JW  DECIMAL(5,2)  NULL,
        CONSTRAINT PK_Matching_Summary_Person PRIMARY KEY (Summary_ID),
        CONSTRAINT FK_MSP_RunLog FOREIGN KEY (Run_ID)
            REFERENCES [{s}].[MatchingResults_v2_RunLog] (run_id)
    );
    CREATE INDEX IX_MSP_Run     ON [{s}].[Matching_Summary_Person] (Run_ID);
    CREATE INDEX IX_MSP_InputID ON [{s}].[Matching_Summary_Person] (Input_ID);
    CREATE INDEX IX_MSP_SDN     ON [{s}].[Matching_Summary_Person] (SDN_UID);
END;
"""

_DDL_SUMMARY_ORG = """
IF OBJECT_ID(N'[{s}].[Matching_Summary_Org]', N'U') IS NULL
BEGIN
    CREATE TABLE [{s}].[Matching_Summary_Org] (
        Summary_ID                                     BIGINT        NOT NULL IDENTITY(1,1),
        Run_ID                                         INT           NOT NULL,
        Input_ID                                       BIGINT        NOT NULL,
        SDN_UID                                        INT           NULL,
        Org_match                                      BIT           NOT NULL CONSTRAINT DF_MSO_OM   DEFAULT 0,
        Input_Org_Name                                 VARCHAR(500)  NULL,
        SDN_Org_Name                                   VARCHAR(500)  NULL,
        SDN_Org_Regular_Match                          VARCHAR(255)  NULL,
        Org_match_JW                                   DECIMAL(5,2)  NULL,
        Org_aka_Match                                  BIT           NOT NULL CONSTRAINT DF_MSO_OAM  DEFAULT 0,
        Input_Org_aka_Name                             VARCHAR(500)  NULL,
        SDN_Org_aka_Name                               VARCHAR(500)  NULL,
        SDN_Org_aka_Match                              VARCHAR(255)  NULL,
        Org_aka_Match_JW                               DECIMAL(5,2)  NULL,
        Org_LinkedTo_Match                             BIT           NOT NULL CONSTRAINT DF_MSO_OLM  DEFAULT 0,
        Input_Org_LinkedTo_Name                        VARCHAR(500)  NULL,
        SDN_Org_LinkedTo_Name                          VARCHAR(500)  NULL,
        SDN_Org_LinkedTo_Match                         VARCHAR(255)  NULL,
        Org_LinkedTo_Match_JW                          DECIMAL(5,2)  NULL,
        Country_City_Match_Org                         BIT           NOT NULL CONSTRAINT DF_MSO_CCO  DEFAULT 0,
        Input_City_Org                                 VARCHAR(255)  NULL,
        SDN_City_Org                                   VARCHAR(255)  NULL,
        Input_Country_Org                              VARCHAR(255)  NULL,
        SDN_Country_Org                                VARCHAR(255)  NULL,
        Country_City_Match_Org_City_JW                 DECIMAL(5,2)  NULL,
        Country_City_Match_Org_Country_JW              DECIMAL(5,2)  NULL,
        Country_City_Match_Org_Aka                     BIT           NOT NULL CONSTRAINT DF_MSO_CCOA DEFAULT 0,
        Input_City_Org_Aka                             VARCHAR(255)  NULL,
        SDN_City_Org_Aka                               VARCHAR(255)  NULL,
        Input_Country_Org_Aka                          VARCHAR(255)  NULL,
        SDN_Country_Org_Aka                            VARCHAR(255)  NULL,
        Country_City_Match_Org_Aka_City_JW             DECIMAL(5,2)  NULL,
        Country_City_Match_Org_Aka_Country_JW          DECIMAL(5,2)  NULL,
        Country_City_Match_Org_LinkedTo                BIT           NOT NULL CONSTRAINT DF_MSO_CCOL DEFAULT 0,
        Input_City_Org_LinkedTo                        VARCHAR(255)  NULL,
        SDN_City_Org_LinkedTo                          VARCHAR(255)  NULL,
        Input_Country_Org_LinkedTo                     VARCHAR(255)  NULL,
        SDN_Country_Org_LinkedTo                       VARCHAR(255)  NULL,
        Country_City_Match_Org_LinkedTo_City_JW        DECIMAL(5,2)  NULL,
        Country_City_Match_Org_LinkedTo_Country_JW     DECIMAL(5,2)  NULL,
        CONSTRAINT PK_Matching_Summary_Org PRIMARY KEY (Summary_ID),
        CONSTRAINT FK_MSO_RunLog FOREIGN KEY (Run_ID)
            REFERENCES [{s}].[MatchingResults_v2_RunLog] (run_id)
    );
    CREATE INDEX IX_MSO_Run     ON [{s}].[Matching_Summary_Org] (Run_ID);
    CREATE INDEX IX_MSO_InputID ON [{s}].[Matching_Summary_Org] (Input_ID);
    CREATE INDEX IX_MSO_SDN     ON [{s}].[Matching_Summary_Org] (SDN_UID);
END;
"""

_SUMMARY_PERSON_INSERT_SQL = """
;WITH AddrMatch AS (
    SELECT Input_Record_ID, SDNEntry_UID AS SDN_UID,
           MAX(City_JaroWinklerSimilarity)    AS City_JW,
           MAX(Country_JaroWinklerSimilarity) AS Country_JW,
           MAX(CASE WHEN City_JaroWinklerSimilarity    >= {ccjw}
                     AND Country_JaroWinklerSimilarity >= {ccjw}
                    THEN 1 ELSE 0 END) AS CC_Match,
           MAX(SourceCity)    AS Input_City,
           MAX(SDNCity)       AS SDN_City,
           MAX(SourceCountry) AS Input_Country,
           MAX(SDNCountry)    AS SDN_Country
    FROM [{{s}}].[MatchingResults_Address]
    WHERE Run_ID = ?
    GROUP BY Input_Record_ID, SDNEntry_UID
),
Reg AS (
    SELECT Input_Record_ID, SDN_UID,
           MAX(FirstName_JaroWinklerSimilarity) AS FN_JW,
           MAX(LastName_JaroWinklerSimilarity)  AS LN_JW,
           MAX(CONCAT_WS(' ', NULLIF(SourceFN,''), NULLIF(SourceMN,''), NULLIF(SourceLN,''))) AS Input_Name,
           MAX(CONCAT_WS(' ', NULLIF(SDNFN,''), NULLIF(SDNLN,'')))                            AS SDN_Name
    FROM [{{s}}].[MatchingResults_Person_Full]
    WHERE Run_ID = ? AND SDN_UID IS NOT NULL
    GROUP BY Input_Record_ID, SDN_UID
),
Aka AS (
    SELECT Input_Record_ID, SDN_UID,
           MAX(FirstName_JaroWinklerSimilarity) AS FN_JW,
           MAX(LastName_JaroWinklerSimilarity)  AS LN_JW,
           MAX(CONCAT_WS(' ', NULLIF(SourceFN,''), NULLIF(SourceMN,''), NULLIF(SourceLN,''))) AS Input_Name,
           MAX(CONCAT_WS(' ', NULLIF(SDNFN,''), NULLIF(SDNLN,'')))                            AS SDN_Name
    FROM [{{s}}].[MatchingResults_AKA]
    WHERE Run_ID = ?
    GROUP BY Input_Record_ID, SDN_UID
),
Lt AS (
    SELECT Input_Record_ID, SDN_UID,
           MAX(FullName_JaroWinklerSimilarity) AS JW,
           MAX(SourceName)    AS Input_Name,
           MAX(LinkedTo_Text) AS SDN_Name
    FROM [{{s}}].[MatchingResults_LinkedTo]
    WHERE Run_ID = ? AND Input_Name_Form IN ('FN_MN_LN','LN_FN_MN')
    GROUP BY Input_Record_ID, SDN_UID
),
Combos AS (
    SELECT Input_Record_ID, SDN_UID FROM Reg
    UNION
    SELECT Input_Record_ID, SDN_UID FROM Aka
    UNION
    SELECT Input_Record_ID, SDN_UID FROM Lt
)
INSERT INTO [{{s}}].[Matching_Summary_Person] (
    Run_ID, Input_ID, SDN_UID,
    Person_Regular_Match, Input_Person_Name, SDN_Person_Name,
    SDN_Person_Regular_Match, Person_Regular_Match_FN_JW, Person_Regular_Match_LN_JW,
    Person_aka_Match, Input_Person_aka_Name, SDN_Person_aka_Name,
    SDN_Person_aka_Match, Person_aka_Match_FN_JW, Person_aka_Match_LN_JW,
    Person_LinkedTo_Match, Input_Person_LinkedTo_Name, SDN_Person_LinkedTo_Name,
    SDN_Person_LinkedTo_Match, Person_LinkedTo_Match_JW,
    Country_City_Match_Person, Input_City_Person, SDN_City_Person, Input_Country_Person, SDN_Country_Person,
    Country_City_Match_Person_City_JW, Country_City_Match_Person_Country_JW,
    Country_City_Match_Person_aka, Input_City_Person_aka, SDN_City_Person_aka, Input_Country_Person_aka, SDN_Country_Person_aka,
    Country_City_Match_Person_aka_City_JW, Country_City_Match_Person_aka_Country_JW,
    Country_City_Match_Person_Linked_To, Input_City_Person_Linked_To, SDN_City_Person_Linked_To, Input_Country_Person_Linked_To, SDN_Country_Person_Linked_To,
    Country_City_Match_Person_Linked_To_City_JW, Country_City_Match_Person_Linked_To_Country_JW
)
SELECT
    ?, c.Input_Record_ID, c.SDN_UID,
    CASE WHEN r.SDN_UID IS NOT NULL THEN 1 ELSE 0 END,
    r.Input_Name, r.SDN_Name,
    CASE WHEN r.SDN_UID IS NOT NULL THEN CAST(c.SDN_UID AS VARCHAR(255)) END,
    r.FN_JW, r.LN_JW,
    CASE WHEN a.SDN_UID IS NOT NULL THEN 1 ELSE 0 END,
    a.Input_Name, a.SDN_Name,
    CASE WHEN a.SDN_UID IS NOT NULL THEN CAST(c.SDN_UID AS VARCHAR(255)) END,
    a.FN_JW, a.LN_JW,
    CASE WHEN l.SDN_UID IS NOT NULL THEN 1 ELSE 0 END,
    l.Input_Name, l.SDN_Name,
    CASE WHEN l.SDN_UID IS NOT NULL THEN CAST(c.SDN_UID AS VARCHAR(255)) END,
    l.JW,
    CASE WHEN r.SDN_UID IS NOT NULL AND ad.CC_Match = 1 THEN 1 ELSE 0 END,
    CASE WHEN r.SDN_UID IS NOT NULL THEN ad.Input_City    END,
    CASE WHEN r.SDN_UID IS NOT NULL THEN ad.SDN_City      END,
    CASE WHEN r.SDN_UID IS NOT NULL THEN ad.Input_Country END,
    CASE WHEN r.SDN_UID IS NOT NULL THEN ad.SDN_Country   END,
    CASE WHEN r.SDN_UID IS NOT NULL THEN ad.City_JW    END,
    CASE WHEN r.SDN_UID IS NOT NULL THEN ad.Country_JW END,
    CASE WHEN a.SDN_UID IS NOT NULL AND ad.CC_Match = 1 THEN 1 ELSE 0 END,
    CASE WHEN a.SDN_UID IS NOT NULL THEN ad.Input_City    END,
    CASE WHEN a.SDN_UID IS NOT NULL THEN ad.SDN_City      END,
    CASE WHEN a.SDN_UID IS NOT NULL THEN ad.Input_Country END,
    CASE WHEN a.SDN_UID IS NOT NULL THEN ad.SDN_Country   END,
    CASE WHEN a.SDN_UID IS NOT NULL THEN ad.City_JW    END,
    CASE WHEN a.SDN_UID IS NOT NULL THEN ad.Country_JW END,
    CASE WHEN l.SDN_UID IS NOT NULL AND ad.CC_Match = 1 THEN 1 ELSE 0 END,
    CASE WHEN l.SDN_UID IS NOT NULL THEN ad.Input_City    END,
    CASE WHEN l.SDN_UID IS NOT NULL THEN ad.SDN_City      END,
    CASE WHEN l.SDN_UID IS NOT NULL THEN ad.Input_Country END,
    CASE WHEN l.SDN_UID IS NOT NULL THEN ad.SDN_Country   END,
    CASE WHEN l.SDN_UID IS NOT NULL THEN ad.City_JW    END,
    CASE WHEN l.SDN_UID IS NOT NULL THEN ad.Country_JW END
FROM Combos c
LEFT JOIN Reg       r  ON r.Input_Record_ID  = c.Input_Record_ID AND r.SDN_UID  = c.SDN_UID
LEFT JOIN Aka       a  ON a.Input_Record_ID  = c.Input_Record_ID AND a.SDN_UID  = c.SDN_UID
LEFT JOIN Lt        l  ON l.Input_Record_ID  = c.Input_Record_ID AND l.SDN_UID  = c.SDN_UID
LEFT JOIN AddrMatch ad ON ad.Input_Record_ID = c.Input_Record_ID AND ad.SDN_UID = c.SDN_UID;
""".format(ccjw=_COUNTRY_CITY_JW_THRESHOLD, s='{s}')

_SUMMARY_PERSON_NOMATCH_INSERT_SQL = """
INSERT INTO [{s}].[Matching_Summary_Person] (Run_ID, Input_ID, SDN_UID)
SELECT ?, nm.Input_Record_ID, NULL
FROM [{s}].[MatchingResults_NoMatch] nm
WHERE nm.Run_ID = ?
"""

_SUMMARY_ORG_INSERT_SQL = """
;WITH AddrMatch AS (
    SELECT Input_Record_ID, SDNEntry_UID AS SDN_UID,
           MAX(City_JaroWinklerSimilarity)    AS City_JW,
           MAX(Country_JaroWinklerSimilarity) AS Country_JW,
           MAX(CASE WHEN City_JaroWinklerSimilarity    >= {ccjw}
                     AND Country_JaroWinklerSimilarity >= {ccjw}
                    THEN 1 ELSE 0 END) AS CC_Match,
           MAX(SourceCity)    AS Input_City,
           MAX(SDNCity)       AS SDN_City,
           MAX(SourceCountry) AS Input_Country,
           MAX(SDNCountry)    AS SDN_Country
    FROM [{{s}}].[MatchingResults_Address]
    WHERE Run_ID = ?
    GROUP BY Input_Record_ID, SDNEntry_UID
),
Org AS (
    SELECT Input_Record_ID, SDN_UID,
           MAX(FullName_JaroWinklerSimilarity) AS JW,
           MAX(SourceOrgName) AS Input_Name,
           MAX(SDNOrgName)    AS SDN_Name
    FROM [{{s}}].[MatchingResults_OrgName]
    WHERE Run_ID = ?
    GROUP BY Input_Record_ID, SDN_UID
),
OrgAka AS (
    SELECT Input_Record_ID, SDN_UID,
           MAX(FullName_JaroWinklerSimilarity) AS JW,
           MAX(SourceOrgName) AS Input_Name,
           MAX(SDNOrgName)    AS SDN_Name
    FROM [{{s}}].[MatchingResults_OrgName_AKA]
    WHERE Run_ID = ?
    GROUP BY Input_Record_ID, SDN_UID
),
Lt AS (
    SELECT Input_Record_ID, SDN_UID,
           MAX(FullName_JaroWinklerSimilarity) AS JW,
           MAX(SourceName)    AS Input_Name,
           MAX(LinkedTo_Text) AS SDN_Name
    FROM [{{s}}].[MatchingResults_LinkedTo]
    WHERE Run_ID = ? AND Input_Name_Form = 'EntityName'
    GROUP BY Input_Record_ID, SDN_UID
),
Combos AS (
    SELECT Input_Record_ID, SDN_UID FROM Org
    UNION
    SELECT Input_Record_ID, SDN_UID FROM OrgAka
    UNION
    SELECT Input_Record_ID, SDN_UID FROM Lt
)
INSERT INTO [{{s}}].[Matching_Summary_Org] (
    Run_ID, Input_ID, SDN_UID,
    Org_match, Input_Org_Name, SDN_Org_Name, SDN_Org_Regular_Match, Org_match_JW,
    Org_aka_Match, Input_Org_aka_Name, SDN_Org_aka_Name, SDN_Org_aka_Match, Org_aka_Match_JW,
    Org_LinkedTo_Match, Input_Org_LinkedTo_Name, SDN_Org_LinkedTo_Name, SDN_Org_LinkedTo_Match, Org_LinkedTo_Match_JW,
    Country_City_Match_Org, Input_City_Org, SDN_City_Org, Input_Country_Org, SDN_Country_Org,
    Country_City_Match_Org_City_JW, Country_City_Match_Org_Country_JW,
    Country_City_Match_Org_Aka, Input_City_Org_Aka, SDN_City_Org_Aka, Input_Country_Org_Aka, SDN_Country_Org_Aka,
    Country_City_Match_Org_Aka_City_JW, Country_City_Match_Org_Aka_Country_JW,
    Country_City_Match_Org_LinkedTo, Input_City_Org_LinkedTo, SDN_City_Org_LinkedTo, Input_Country_Org_LinkedTo, SDN_Country_Org_LinkedTo,
    Country_City_Match_Org_LinkedTo_City_JW, Country_City_Match_Org_LinkedTo_Country_JW
)
SELECT
    ?, c.Input_Record_ID, c.SDN_UID,
    CASE WHEN o.SDN_UID IS NOT NULL THEN 1 ELSE 0 END,
    o.Input_Name, o.SDN_Name,
    CASE WHEN o.SDN_UID IS NOT NULL THEN CAST(c.SDN_UID AS VARCHAR(255)) END,
    o.JW,
    CASE WHEN oa.SDN_UID IS NOT NULL THEN 1 ELSE 0 END,
    oa.Input_Name, oa.SDN_Name,
    CASE WHEN oa.SDN_UID IS NOT NULL THEN CAST(c.SDN_UID AS VARCHAR(255)) END,
    oa.JW,
    CASE WHEN l.SDN_UID IS NOT NULL THEN 1 ELSE 0 END,
    l.Input_Name, l.SDN_Name,
    CASE WHEN l.SDN_UID IS NOT NULL THEN CAST(c.SDN_UID AS VARCHAR(255)) END,
    l.JW,
    CASE WHEN o.SDN_UID IS NOT NULL AND ad.CC_Match = 1 THEN 1 ELSE 0 END,
    CASE WHEN o.SDN_UID IS NOT NULL THEN ad.Input_City    END,
    CASE WHEN o.SDN_UID IS NOT NULL THEN ad.SDN_City      END,
    CASE WHEN o.SDN_UID IS NOT NULL THEN ad.Input_Country END,
    CASE WHEN o.SDN_UID IS NOT NULL THEN ad.SDN_Country   END,
    CASE WHEN o.SDN_UID IS NOT NULL THEN ad.City_JW    END,
    CASE WHEN o.SDN_UID IS NOT NULL THEN ad.Country_JW END,
    CASE WHEN oa.SDN_UID IS NOT NULL AND ad.CC_Match = 1 THEN 1 ELSE 0 END,
    CASE WHEN oa.SDN_UID IS NOT NULL THEN ad.Input_City    END,
    CASE WHEN oa.SDN_UID IS NOT NULL THEN ad.SDN_City      END,
    CASE WHEN oa.SDN_UID IS NOT NULL THEN ad.Input_Country END,
    CASE WHEN oa.SDN_UID IS NOT NULL THEN ad.SDN_Country   END,
    CASE WHEN oa.SDN_UID IS NOT NULL THEN ad.City_JW    END,
    CASE WHEN oa.SDN_UID IS NOT NULL THEN ad.Country_JW END,
    CASE WHEN l.SDN_UID IS NOT NULL AND ad.CC_Match = 1 THEN 1 ELSE 0 END,
    CASE WHEN l.SDN_UID IS NOT NULL THEN ad.Input_City    END,
    CASE WHEN l.SDN_UID IS NOT NULL THEN ad.SDN_City      END,
    CASE WHEN l.SDN_UID IS NOT NULL THEN ad.Input_Country END,
    CASE WHEN l.SDN_UID IS NOT NULL THEN ad.SDN_Country   END,
    CASE WHEN l.SDN_UID IS NOT NULL THEN ad.City_JW    END,
    CASE WHEN l.SDN_UID IS NOT NULL THEN ad.Country_JW END
FROM Combos c
LEFT JOIN Org       o  ON o.Input_Record_ID  = c.Input_Record_ID AND o.SDN_UID  = c.SDN_UID
LEFT JOIN OrgAka    oa ON oa.Input_Record_ID = c.Input_Record_ID AND oa.SDN_UID = c.SDN_UID
LEFT JOIN Lt        l  ON l.Input_Record_ID  = c.Input_Record_ID AND l.SDN_UID  = c.SDN_UID
LEFT JOIN AddrMatch ad ON ad.Input_Record_ID = c.Input_Record_ID AND ad.SDN_UID = c.SDN_UID;
""".format(ccjw=_COUNTRY_CITY_JW_THRESHOLD, s='{s}')

_SUMMARY_ORG_NOMATCH_INSERT_SQL = """
INSERT INTO [{s}].[Matching_Summary_Org] (Run_ID, Input_ID, SDN_UID)
SELECT ?, nm.Input_Record_ID, NULL
FROM [{s}].[MatchingResults_NoMatch] nm
WHERE nm.Run_ID = ?
"""


def populate_matching_summary(conn, schema: str, run_id: int) -> tuple:
    """
    Post-processing step: populate Matching_Summary_Person and
    Matching_Summary_Org from the already-flushed MatchingResults_* tables
    for this run.  Returns (person_rows, org_rows).
    """
    cur = conn.cursor()

    cur.execute(_SUMMARY_PERSON_INSERT_SQL.replace('{s}', schema),
                run_id, run_id, run_id, run_id, run_id)
    person_rows = cur.rowcount
    cur.execute(_SUMMARY_PERSON_NOMATCH_INSERT_SQL.replace('{s}', schema),
                run_id, run_id)
    person_rows += cur.rowcount

    cur.execute(_SUMMARY_ORG_INSERT_SQL.replace('{s}', schema),
                run_id, run_id, run_id, run_id, run_id)
    org_rows = cur.rowcount
    cur.execute(_SUMMARY_ORG_NOMATCH_INSERT_SQL.replace('{s}', schema),
                run_id, run_id)
    org_rows += cur.rowcount

    conn.commit()
    return person_rows, org_rows


def _field_scores(inp: str, sdn: str) -> float:
    """Return jw_pct (0–100) for one pair of normalised strings.
    Calls _jaro_winkler_fast directly — skips edit-distance computation
    that _score() performs but that is unused here."""
    iv, sv = inp.lower().strip(), sdn.lower().strip()
    if not iv or not sv:
        return 0.00
    return round(_jaro_winkler_fast(iv, sv) * 100, 2)


def score_name_candidate(fn_nm: str, ln_nm: str,
                          uid: int, name_idx: dict) -> list:
    """
    Compute JaroWinkler similarity for one (normalised input name, SDN UID) pair.
    Returns [fn_jw, ln_jw] — all values 0–100.
    """
    # Use normalized (_NM) SDN names so both sides have punctuation stripped
    sdn_ln = name_idx['uid_to_ln_nm'].get(uid, '')
    sdn_fn = name_idx['uid_to_fn_nm'].get(uid, '')

    fn_jw = _field_scores(fn_nm, sdn_fn)
    ln_jw = _field_scores(ln_nm, sdn_ln)

    return [fn_jw, ln_jw]


def _score_aka(fn_nm: str, ln_nm: str,
               aka_fn: str, aka_ln: str,
               strip_pat: re.Pattern) -> list:
    """
    Compute JaroWinkler similarity for one (normalised input name, AKA name) pair.
    Both input and AKA names use their _NM (punctuation-stripped) forms.
    Returns [fn_jw, ln_jw] — all values 0–100.
    """
    aka_fn_nm = normalize(aka_fn or '', strip_pat)
    aka_ln_nm = normalize(aka_ln or '', strip_pat)

    fn_jw = _field_scores(fn_nm, aka_fn_nm)
    ln_jw = _field_scores(ln_nm, aka_ln_nm)

    return [fn_jw, ln_jw]


def score_org_name(src_nm: str, sdn_nm: str,
                   jw_threshold: float,
                   word_match: bool = True,
                   entity_map: dict = None) -> tuple:
    """
    Org name comparison.
    Returns (src_word_count, sdn_word_count,
             words_matching_jw, fullname_jw_similarity).

    Full-string JW similarity is always computed on the FULL strings (not stripped).

    When entity_map is provided, word counts are computed on the name with any
    matching entity type marker (prefix or suffix) stripped from both sides.
    If only one side has a marker, or markers don't match, full word counts are used.

    When word_match=True (default):
      For each source word, scan every SDN word and count matches where
      JaroWinkler similarity >= jw_threshold.

    When word_match=False (--no-org-word-match):
      Word counts are still computed but per-word scoring is skipped;
      WordNumberMatchingJaroWinkler will be 0.
    """
    src_lower = src_nm.lower() if src_nm else ''
    sdn_lower = sdn_nm.lower() if sdn_nm else ''

    # Full-string JW similarity (always computed on FULL strings).
    # Call _jaro_winkler_fast directly — skips edit-distance computation
    # that _score() performs but that is unused in this function.
    if src_lower and sdn_lower:
        full_jw = round(_jaro_winkler_fast(src_lower, sdn_lower) * 100, 2)
    else:
        full_jw = 0.0

    # Determine words to use for counting (may be stripped of entity type marker)
    if entity_map is not None:
        src_words_upper = src_nm.upper().split() if src_nm else []
        sdn_words_upper = sdn_nm.upper().split() if sdn_nm else []
        # Try suffix strip first
        src_suf, _, src_rest_suf = _strip_entity_suffix(src_words_upper, entity_map)
        sdn_suf, _, sdn_rest_suf = _strip_entity_suffix(sdn_words_upper, entity_map)
        if src_suf is not None and sdn_suf is not None and src_suf == sdn_suf:
            src_words = [w.lower() for w in src_rest_suf]
            sdn_words = [w.lower() for w in sdn_rest_suf]
        else:
            # Try prefix strip
            src_pre, _, src_rest_pre = _strip_entity_prefix(src_words_upper, entity_map)
            sdn_pre, _, sdn_rest_pre = _strip_entity_prefix(sdn_words_upper, entity_map)
            if src_pre is not None and sdn_pre is not None and src_pre == sdn_pre:
                src_words = [w.lower() for w in src_rest_pre]
                sdn_words = [w.lower() for w in sdn_rest_pre]
            else:
                src_words = src_lower.split()
                sdn_words = sdn_lower.split()
    else:
        src_words = src_lower.split()
        sdn_words = sdn_lower.split()

    if not src_words or not sdn_words or not word_match:
        return len(src_words), len(sdn_words), 0, full_jw

    jw_m = 0
    for sw in src_words:
        for dw in sdn_words:
            if _jaro_winkler_fast(sw, dw) >= jw_threshold:
                jw_m += 1
                break   # count this source word once; move to next
    return len(src_words), len(sdn_words), jw_m, full_jw


# Entity / Entity AKA / Entity Related To match gate.
# A pair is a match only if:
#   abs(SourceNumberofWords - SDNNumberofWords)            in (0, 1)
#   and abs(SourceNumberofWords - WordNumberMatchingJaroWinkler) in (0, 1)
#   and FullName_JaroWinklerSimilarity                     >= ENTITY_GATE_JW
ENTITY_GATE_JW = 90.0


def _entity_match_gate(src_wc: int, sdn_wc: int, jw_m: int, full_jw: float) -> bool:
    return (abs(src_wc - sdn_wc) in (0, 1)
            and abs(src_wc - jw_m) in (0, 1)
            and full_jw >= ENTITY_GATE_JW)


# ---------------------------------------------------------------------------
# Entity Suffix-aware match gate.
#
# Refinement applied on top of _entity_match_gate for Entity / Entity AKA /
# Entity Related-To matches:
#   1. If both names end with the same recognized Entity Suffix
#      (e.g. "LLC" / "Limited Liability Company"), evaluate the words
#      OTHER than the suffix:
#         - 1 or 2 other words  -> ALL of them must JW-match
#         - 3+ other words      -> at least (count - 1) must JW-match
#      This decides the match outright (no full-name JW threshold applied).
#   2. If the Entity Suffix does not match, or is not present in both
#      names, fall back to the existing _entity_match_gate().
# ---------------------------------------------------------------------------

def _strip_entity_suffix(words: list, entity_map: dict = None) -> tuple:
    """
    If `words` (uppercase tokens) ends with a recognized entity type phrase
    (abbreviated or expanded form), return
    (canonical_form, word_count, remaining_words).
    Otherwise return (None, 0, words).

    Uses entity_map['suffix_phrases'] and entity_map['canon'] when provided.
    """
    if entity_map is None:
        return None, 0, words
    suffix_phrases = entity_map.get('suffix_phrases', [])
    canon = entity_map.get('canon', {})
    for phrase in suffix_phrases:
        pw = phrase.split()
        n = len(pw)
        if len(words) >= n and words[-n:] == pw:
            return canon.get(phrase, phrase), n, words[:-n]
    return None, 0, words


def _strip_entity_prefix(words: list, entity_map: dict = None) -> tuple:
    """
    If `words` (uppercase tokens) STARTS WITH a recognized entity type phrase
    (abbreviated or expanded form), return
    (canonical_form, word_count, remaining_words).
    Otherwise return (None, 0, words).

    Uses entity_map['prefix_phrases'] and entity_map['canon'] when provided.
    """
    if entity_map is None:
        return None, 0, words
    prefix_phrases = entity_map.get('prefix_phrases', [])
    canon = entity_map.get('canon', {})
    for phrase in prefix_phrases:
        pw = phrase.split()
        n = len(pw)
        if len(words) >= n and words[:n] == pw:
            return canon.get(phrase, phrase), n, words[n:]
    return None, 0, words


def _entity_type_gate(src_nm: str, sdn_nm: str, jw_threshold: float,
                      entity_map: dict = None):
    """
    Returns True/False if an entity type marker (prefix or suffix) is present
    and matches in both names (decisive — overrides _entity_match_gate), or
    None if no matching entity type marker is found (caller falls back to
    _entity_match_gate).

    Checks suffix first; if no matching suffix, checks prefix.
    """
    src_words = src_nm.upper().split() if src_nm else []
    sdn_words = sdn_nm.upper().split() if sdn_nm else []

    # --- Try suffix first ---
    src_suffix, _, src_rest = _strip_entity_suffix(src_words, entity_map)
    sdn_suffix, _, sdn_rest = _strip_entity_suffix(sdn_words, entity_map)

    if src_suffix is not None and sdn_suffix is not None and src_suffix == sdn_suffix:
        n_other = len(src_rest)
        if n_other == 0:
            return True
        jw_m = sum(
            1 for sw in src_rest
            if any(_jaro_winkler_fast(sw.lower(), dw.lower()) >= jw_threshold
                   for dw in sdn_rest)
        )
        required = n_other if n_other <= 2 else n_other - 1
        return jw_m >= required

    # --- Try prefix ---
    src_prefix, _, src_rest_p = _strip_entity_prefix(src_words, entity_map)
    sdn_prefix, _, sdn_rest_p = _strip_entity_prefix(sdn_words, entity_map)

    if src_prefix is not None and sdn_prefix is not None and src_prefix == sdn_prefix:
        n_other = len(src_rest_p)
        if n_other == 0:
            return True
        jw_m = sum(
            1 for sw in src_rest_p
            if any(_jaro_winkler_fast(sw.lower(), dw.lower()) >= jw_threshold
                   for dw in sdn_rest_p)
        )
        required = n_other if n_other <= 2 else n_other - 1
        return jw_m >= required

    return None


def _entity_match_gate_v2(src_nm: str, sdn_nm: str,
                          src_wc: int, sdn_wc: int, jw_m: int, full_jw: float,
                          jw_threshold: float,
                          entity_map: dict = None) -> bool:
    type_result = _entity_type_gate(src_nm, sdn_nm, jw_threshold, entity_map)
    if type_result is not None:
        return type_result
    return _entity_match_gate(src_wc, sdn_wc, jw_m, full_jw)


def flush_full_results(conn, schema: str, rows: list, batch_size: int = 1000):
    if not rows:
        return
    sql = _FULL_INSERT_SQL.replace('{s}', schema)
    cur = conn.cursor()
    cur.fast_executemany = True
    for i in range(0, len(rows), batch_size):
        cur.executemany(sql, rows[i:i + batch_size])
    conn.commit()


def flush_no_match(conn, schema: str, rows: list, batch_size: int = 5000):
    """Write (Run_ID, Input_Record_ID, SDN_UID) slim rows to MatchingResults_Person_NoMatch."""
    if not rows:
        return
    sql = _NO_MATCH_INSERT_SQL.replace('{s}', schema)
    cur = conn.cursor()
    cur.fast_executemany = True
    for i in range(0, len(rows), batch_size):
        cur.executemany(sql, rows[i:i + batch_size])
    conn.commit()


def flush_aka_results(conn, schema: str, rows: list, batch_size: int = 1000):
    """Write scored AKA rows to MatchingResults_AKA."""
    if not rows:
        return
    sql = _AKA_INSERT_SQL.replace('{s}', schema)
    cur = conn.cursor()
    cur.fast_executemany = True
    for i in range(0, len(rows), batch_size):
        cur.executemany(sql, rows[i:i + batch_size])
    conn.commit()


def flush_aka_no_match(conn, schema: str, rows: list, batch_size: int = 5000):
    """Write slim AKA no-match rows to MatchingResults_AKA_NoMatch."""
    if not rows:
        return
    sql = _AKA_NO_MATCH_INSERT_SQL.replace('{s}', schema)
    cur = conn.cursor()
    cur.fast_executemany = True
    for i in range(0, len(rows), batch_size):
        cur.executemany(sql, rows[i:i + batch_size])
    conn.commit()


def flush_org_results(conn, schema: str, rows: list, batch_size: int = 1000):
    """Write scored org-name rows to MatchingResults_OrgName."""
    if not rows:
        return
    sql = _ORG_INSERT_SQL.replace('{s}', schema)
    cur = conn.cursor()
    cur.fast_executemany = True
    for i in range(0, len(rows), batch_size):
        cur.executemany(sql, rows[i:i + batch_size])
    conn.commit()


def flush_org_no_match(conn, schema: str, rows: list, batch_size: int = 5000):
    """Write slim org-name no-match rows to MatchingResults_OrgName_NoMatch."""
    if not rows:
        return
    sql = _ORG_NO_MATCH_INSERT_SQL.replace('{s}', schema)
    cur = conn.cursor()
    cur.fast_executemany = True
    for i in range(0, len(rows), batch_size):
        cur.executemany(sql, rows[i:i + batch_size])
    conn.commit()


def flush_org_aka_results(conn, schema: str, rows: list, batch_size: int = 1000):
    """Write scored org-name AKA rows to MatchingResults_OrgName_AKA."""
    if not rows:
        return
    sql = _ORG_AKA_INSERT_SQL.replace('{s}', schema)
    cur = conn.cursor()
    cur.fast_executemany = True
    for i in range(0, len(rows), batch_size):
        cur.executemany(sql, rows[i:i + batch_size])
    conn.commit()


def flush_org_aka_no_match(conn, schema: str, rows: list, batch_size: int = 5000):
    """Write slim org-name AKA no-match rows to MatchingResults_OrgName_AKA_NoMatch."""
    if not rows:
        return
    sql = _ORG_AKA_NO_MATCH_INSERT_SQL.replace('{s}', schema)
    cur = conn.cursor()
    cur.fast_executemany = True
    for i in range(0, len(rows), batch_size):
        cur.executemany(sql, rows[i:i + batch_size])
    conn.commit()


def seed_addr_match_types(conn, schema: str):
    """Insert the 32 fixed rows into StreetAddressMatchType if the table is empty."""
    cur = conn.cursor()
    count = cur.execute(
        f"SELECT COUNT(*) FROM [{schema}].[StreetAddressMatchType]"
    ).fetchone()[0]
    if count == 0:
        sql = (f"INSERT INTO [{schema}].[StreetAddressMatchType] "
               f"(ID, Method, StreetAddressMatchType) VALUES (?,?,?)")
        cur.fast_executemany = True
        cur.executemany(sql, _ADDR_MATCH_TYPE_ROWS)
        conn.commit()


def flush_addr_full_results(conn, schema: str, rows: list, batch_size: int = 1000):
    """Write scored address rows to MatchingResults_Address."""
    if not rows:
        return
    sql = _ADDR_FULL_INSERT_SQL.replace('{s}', schema)
    cur = conn.cursor()
    cur.fast_executemany = True
    for i in range(0, len(rows), batch_size):
        cur.executemany(sql, rows[i:i + batch_size])
    conn.commit()


def flush_addr_no_match(conn, schema: str, rows: list, batch_size: int = 5000):
    """Write slim address no-match rows to MatchingResults_Address_NoMatch."""
    if not rows:
        return
    sql = _ADDR_NO_MATCH_INSERT_SQL.replace('{s}', schema)
    cur = conn.cursor()
    cur.fast_executemany = True
    for i in range(0, len(rows), batch_size):
        cur.executemany(sql, rows[i:i + batch_size])
    conn.commit()


def flush_linked_to_results(conn, schema: str, rows: list, batch_size: int = 1000):
    """Write linked-to rows to MatchingResults_LinkedTo."""
    if not rows:
        return
    sql = _LINKED_TO_INSERT_SQL.replace('{s}', schema)
    cur = conn.cursor()
    cur.fast_executemany = True
    for i in range(0, len(rows), batch_size):
        cur.executemany(sql, rows[i:i + batch_size])
    conn.commit()


def flush_linked_to_no_match(conn, schema: str, rows: list, batch_size: int = 5000):
    """Write linked-to no-match rows to MatchingResults_LinkedTo_NoMatch."""
    if not rows:
        return
    sql = _LINKED_TO_NO_MATCH_INSERT_SQL.replace('{s}', schema)
    cur = conn.cursor()
    cur.fast_executemany = True
    for i in range(0, len(rows), batch_size):
        cur.executemany(sql, rows[i:i + batch_size])
    conn.commit()


def flush_phone_results(conn, schema: str, rows: list, batch_size: int = 1000):
    """Write phone rows to MatchingResults_Phone."""
    if not rows:
        return
    sql = _PHONE_INSERT_SQL.replace('{s}', schema)
    cur = conn.cursor()
    cur.fast_executemany = True
    for i in range(0, len(rows), batch_size):
        cur.executemany(sql, rows[i:i + batch_size])
    conn.commit()


# ---------------------------------------------------------------------------
# SDN publish info lookup
# ---------------------------------------------------------------------------

def get_sdn_publish_info(sdn_cs: str) -> tuple:
    """Return (ID, Publish_Date str) of the most recent SDN_PublishInfo row."""
    try:
        with pyodbc.connect(sdn_cs) as conn:
            row = conn.cursor().execute(
                "SELECT TOP 1 ID, CONVERT(VARCHAR,Publish_Date,23) "
                "FROM dbo.SDN_PublishInfo ORDER BY ID DESC"
            ).fetchone()
        if row:
            return int(row[0]), str(row[1])
    except Exception:
        pass
    return None, None


# ---------------------------------------------------------------------------
# Dummy record generation (QA / threshold validation)
# ---------------------------------------------------------------------------

def _perturb_to_jw_range(s: str, rng: random.Random,
                          lo: float = 0.75, hi: float = 0.99,
                          max_iter: int = 2000) -> str:
    """
    Return a perturbed copy of s whose JaroWinkler similarity to the original
    falls in [lo, hi].  Substitutions are biased toward the back third of the
    string so the JW prefix bonus is preserved.
    Returns s unchanged if convergence fails.
    """
    if not s or len(s) < 2:
        return s
    ALPHA = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
    # Only mutate non-space positions
    mutable = [i for i, c in enumerate(s) if c != ' ']
    if not mutable:
        return s
    # Prefer the back two-thirds so the prefix stays intact
    back = [i for i in mutable if i >= max(1, len(s) // 3)]
    pool = back if back else mutable

    for _ in range(max_iter):
        n_muts = rng.randint(1, min(4, len(pool)))
        positions = rng.sample(pool, min(n_muts, len(pool)))
        candidate = list(s)
        for i in positions:
            orig = candidate[i]
            choices = [c for c in ALPHA if c != orig]
            candidate[i] = rng.choice(choices)
        result = ''.join(candidate)
        jw = _jaro_winkler_similarity(s, result)
        if lo <= jw <= hi:
            return result

    # Systematic fallback: every single-char substitution from the back
    for i in sorted(pool, reverse=True):
        orig = s[i]
        chars = list(s)
        for c in ALPHA:
            if c == orig:
                continue
            chars[i] = c
            result = ''.join(chars)
            jw = _jaro_winkler_similarity(s, result)
            if lo <= jw <= hi:
                return result
    return s   # convergence failed — return original


def generate_dummy_records(
    n: int,
    sdn_entry_map: dict,
    aka_by_sdn: dict,
    entity_org_map: dict,
    entity_aka_norm: dict,
    linked_to_by_uid: dict,
    addresses: list,
    strip_pat: re.Pattern,
    jw_lo: float = 0.75,
    jw_hi: float = 0.99,
    addr_fraction: float = 0.30,
    seed: int = None,
) -> list:
    """
    Generate n synthetic InputRecord objects whose field values are designed to
    land in the [jw_lo, jw_hi] JW-similarity band against real SDN entries.

    Categories (approx. equal slices; remainder allocated to P):
      P    — Person Name     (sdnEntry Individual FN/LN)
      PAKA — AKA Person Name (Individual AKA FN/LN)
      E    — Entity Name     (sdnEntry Entity org name)
      EAKA — Entity AKA Name (Entity AKA org name)
      LT   — Linked-to text  (Entity record targeting a Linked-to string)

    ~addr_fraction of records from all categories also receive a City + Country
    pair perturbed from a real SDN address record (category-5 embedding).

    external_id is set to 'DUMMY-{CAT}-{seq:04d}' for traceability.
    Returned records have raw fields populated; normalize_input() must be called
    by the caller before they enter the main matching loop.
    """
    rng = random.Random(seed)

    def _nm(s):
        return normalize(s or '', strip_pat)

    # Build flat, pre-normalized sample pools; filter out very short strings
    ind_pool  = [(uid, _nm(fn), _nm(ln))
                 for uid, (fn, ln, _) in sdn_entry_map.items()
                 if len(_nm(fn)) >= 3 and len(_nm(ln)) >= 3]
    aka_pool  = [(uid, _nm(afn), _nm(aln))
                 for uid, akas in aka_by_sdn.items()
                 for (_, afn, aln, _) in akas
                 if len(_nm(afn)) >= 3 and len(_nm(aln)) >= 3]
    ent_pool  = [(uid, nm)
                 for uid, (_, nm) in entity_org_map.items()
                 if nm and len(nm) >= 3]
    eaka_pool = [(suid, anm)
                 for (suid, _), (_, anm, _) in entity_aka_norm.items()
                 if anm and len(anm) >= 3]
    lt_pool   = [(uid, lt_nm)
                 for uid, occs in linked_to_by_uid.items()
                 for (_, _, lt_nm, _) in occs
                 if lt_nm and len(lt_nm) >= 3]
    addr_pool = [a for a in addresses
                 if (a.city_nm    and len(a.city_nm)    >= 3)
                 or (a.country_nm and len(a.country_nm) >= 3)]
    addr_by_sdn_uid: dict = defaultdict(list)
    for a in addr_pool:
        addr_by_sdn_uid[a.sdn_entry_uid].append(a)

    active = {
        'P':    bool(ind_pool),
        'PAKA': bool(aka_pool),
        'E':    bool(ent_pool),
        'EAKA': bool(eaka_pool),
        'LT':   bool(lt_pool),
    }
    n_active = sum(active.values())
    if n_active == 0:
        print("  [dummy] No usable SDN data found — 0 dummy records generated.")
        return []

    # Equal slices; remainder goes to first active category
    slice_sz = max(1, n // 5)
    counts = {cat: (slice_sz if active[cat] else 0) for cat in active}
    remainder = n - sum(counts.values())
    for cat in counts:
        if counts[cat] > 0:
            counts[cat] += remainder
            break

    recs: list = []
    seq = 0

    def _maybe_addr(rec: InputRecord, target_sdn_uid: int = None):
        """Optionally embed a perturbed city+country from a real SDN address.

        Prefers an address belonging to the same SDN entity that the name
        was perturbed from, so the Address pass produces a row whose
        SDNEntry_UID matches the name match's SDN_UID -- otherwise the
        Country_City_Match_* summary flags can never be set, since they
        require the address match and name match to share the same SDN_UID.
        Falls back to a random SDN address if the target entity has none.
        """
        if not addr_pool or rng.random() > addr_fraction:
            return
        pool = addr_by_sdn_uid.get(target_sdn_uid) if target_sdn_uid is not None else None
        sa = rng.choice(pool) if pool else rng.choice(addr_pool)
        if sa.city_nm and len(sa.city_nm) >= 3:
            rec.city = _perturb_to_jw_range(sa.city_nm, rng, jw_lo, jw_hi)
        if sa.country_nm and len(sa.country_nm) >= 3:
            rec.country = _perturb_to_jw_range(sa.country_nm, rng, jw_lo, jw_hi)

    # Category P — Person Name (sdnEntry Individual)
    for _ in range(counts['P']):
        uid, fn_nm, ln_nm = rng.choice(ind_pool)
        rec = InputRecord(
            entity_type='Individual',
            first_name =_perturb_to_jw_range(fn_nm, rng, jw_lo, jw_hi),
            last_name  =_perturb_to_jw_range(ln_nm, rng, jw_lo, jw_hi),
            external_id=f'DUMMY-P-{seq:04d}',
        )
        _maybe_addr(rec, uid)
        recs.append(rec)
        seq += 1

    # Category PAKA — AKA Person Name (Individual AKA)
    for _ in range(counts['PAKA']):
        uid, afn_nm, aln_nm = rng.choice(aka_pool)
        rec = InputRecord(
            entity_type='Individual',
            first_name =_perturb_to_jw_range(afn_nm, rng, jw_lo, jw_hi),
            last_name  =_perturb_to_jw_range(aln_nm, rng, jw_lo, jw_hi),
            external_id=f'DUMMY-PAKA-{seq:04d}',
        )
        _maybe_addr(rec, uid)
        recs.append(rec)
        seq += 1

    # Category E — Entity Name (sdnEntry Entity)
    for _ in range(counts['E']):
        uid, ent_nm = rng.choice(ent_pool)
        rec = InputRecord(
            entity_type='Entity',
            entity_name=_perturb_to_jw_range(ent_nm, rng, jw_lo, jw_hi),
            external_id=f'DUMMY-E-{seq:04d}',
        )
        _maybe_addr(rec, uid)
        recs.append(rec)
        seq += 1

    # Category EAKA — Entity AKA Name
    for _ in range(counts['EAKA']):
        suid, anm = rng.choice(eaka_pool)
        rec = InputRecord(
            entity_type='Entity',
            entity_name=_perturb_to_jw_range(anm, rng, jw_lo, jw_hi),
            external_id=f'DUMMY-EAKA-{seq:04d}',
        )
        _maybe_addr(rec, suid)
        recs.append(rec)
        seq += 1

    # Category LT — Linked-to text (Entity path: entity_name_nm vs lt_nm)
    for _ in range(counts['LT']):
        uid, lt_nm = rng.choice(lt_pool)
        rec = InputRecord(
            entity_type='Entity',
            entity_name=_perturb_to_jw_range(lt_nm, rng, jw_lo, jw_hi),
            external_id=f'DUMMY-LT-{seq:04d}',
        )
        _maybe_addr(rec, uid)
        recs.append(rec)
        seq += 1

    rng.shuffle(recs)
    return recs


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Parallel cache precompute — module-level so workers can pickle them.
#
# Each worker process is initialised once with the full SDN reference data
# via _worker_init (called by ProcessPoolExecutor's initializer=).  Subsequent
# tasks in that process just call _score_indiv_keys_batch / _score_entity_keys_batch,
# reading from the module-global _WK dict without re-pickling the SDN data.
# ---------------------------------------------------------------------------

_WK: dict = {}   # populated by _worker_init in each worker process


def _worker_init(sdn_entry_map, name_idx, aka_by_sdn,
                 entity_org_map, entity_aka_norm,
                 jw_name_pct, jw_org_threshold, jw_org_aka_threshold,
                 run_org_word_match, strip_pat, entity_map=None):
    """Called once per worker process; stores SDN reference data in _WK."""
    global _WK
    _WK = dict(
        sdn_entry_map        = sdn_entry_map,
        name_idx             = name_idx,
        aka_by_sdn           = aka_by_sdn,
        entity_org_map       = entity_org_map,
        entity_aka_norm      = entity_aka_norm,
        jw_name_pct          = jw_name_pct,
        jw_org_threshold     = jw_org_threshold,
        jw_org_aka_threshold = jw_org_aka_threshold,
        run_org_word_match   = run_org_word_match,
        strip_pat            = strip_pat,
        entity_map           = entity_map,
    )


def _score_indiv_keys_batch(keys_batch: list) -> dict:
    """
    Score a batch of unique (fn_nm, mn_nm, ln_nm) keys against all SDN
    individuals (Pass 1) and all individual AKAs (Pass 2).
    Returns {key: cache_entry_dict}.
    Reads SDN data from the module-global _WK (set by _worker_init).
    """
    wk                  = _WK
    jw_name_pct         = wk['jw_name_pct']
    sdn_entry_map       = wk['sdn_entry_map']
    name_idx            = wk['name_idx']
    aka_by_sdn          = wk['aka_by_sdn']
    strip_pat           = wk['strip_pat']

    result: dict = {}
    for indiv_key in keys_batch:
        fn_nm, mn_nm, ln_nm = indiv_key

        # Pass 1 — score ALL SDN Individuals; FN+LN JW only
        _p1_full: list = []
        for _uid, (_sdn_fn, _sdn_ln, _sdn_type) in sdn_entry_map.items():
            _sc = score_name_candidate(fn_nm, ln_nm, _uid, name_idx)
            if _sc[0] >= jw_name_pct and _sc[1] >= jw_name_pct:
                _p1_full.append(
                    (fn_nm, mn_nm, ln_nm,
                     _uid, _sdn_fn, _sdn_ln, _sdn_type, 'sdnEntry')
                    + tuple(_sc) + (1,))

        # Pass 2 — score ALL Individual AKAs; FN+LN JW only
        _p2_aka: list = []
        for _suid, _akas in aka_by_sdn.items():
            for _auid, _afn, _aln, _acat in _akas:
                _asc = _score_aka(fn_nm, ln_nm, _afn or '', _aln or '', strip_pat)
                if _asc[0] >= jw_name_pct and _asc[1] >= jw_name_pct:
                    _p2_aka.append(
                        (_suid, _auid, _s(_acat),
                         fn_nm, mn_nm, ln_nm,
                         _s(_afn), _s(_aln))
                        + tuple(_asc) + (1,))

        result[indiv_key] = {
            'full':     _p1_full,
            'aka':      _p2_aka,
            'has_full': bool(_p1_full) or bool(_p2_aka),
        }
    return result


def _score_entity_keys_batch(keys_batch: list) -> dict:
    """
    Score a batch of unique entity_name_nm strings against all SDN entities
    (Pass 3) and entity AKAs (Pass 3b).
    Returns {key: cache_entry_dict}.
    Reads SDN data from the module-global _WK (set by _worker_init).
    """
    wk                   = _WK
    entity_org_map       = wk['entity_org_map']
    entity_aka_norm      = wk['entity_aka_norm']
    jw_org_threshold     = wk['jw_org_threshold']
    jw_org_aka_threshold = wk['jw_org_aka_threshold']
    run_org_word_match   = wk['run_org_word_match']

    entity_map           = wk.get('entity_map')

    result: dict = {}
    for src_org in keys_batch:
        # Pass 3 — Entity sdnEntry full cross-comparison
        _p3_org: list = []
        for _uid, (_sdn_raw, _sdn_nm) in entity_org_map.items():
            _sc = score_org_name(src_org, _sdn_nm, jw_org_threshold, run_org_word_match,
                                 entity_map)
            if _entity_match_gate_v2(src_org, _sdn_nm, *_sc, jw_org_threshold,
                                     entity_map):
                _p3_org.append((_uid, _sdn_raw, _sdn_nm) + tuple(_sc))

        # Pass 3b — Entity AKA full cross-comparison
        _p3b_aka: list = []
        for (_suid, _auid), (_araw, _anm, _acat) in entity_aka_norm.items():
            _sc = score_org_name(src_org, _anm, jw_org_aka_threshold, run_org_word_match,
                                 entity_map)
            if _entity_match_gate_v2(src_org, _anm, *_sc, jw_org_aka_threshold,
                                     entity_map):
                _p3b_aka.append(
                    (_suid, _auid, _s(_acat), _araw, _anm) + tuple(_sc))

        result[src_org] = {
            'org':      _p3_org,
            'org_aka':  _p3b_aka,
            'has_full': bool(_p3_org) or bool(_p3b_aka),
        }
    return result


def _precompute_duckdb(
    indiv_name_cache: dict,
    entity_name_cache: dict,
    unique_indiv_keys: list,
    unique_entity_keys: list,
    name_idx: dict,
    aka_by_sdn: dict,
    sdn_entry_map: dict,
    entity_org_map: dict,
    entity_aka_norm: dict,
    jw_name_pct: float,          # 0-100 scale  (e.g. 75.0)
    jw_org_threshold: float,     # 0-1  scale  (e.g. 0.75)
    jw_org_aka_threshold: float,
    run_org_word_match: bool,
    strip_pat,
    entity_map: dict = None,
) -> None:
    """
    Populate indiv_name_cache and entity_name_cache using DuckDB's built-in
    jaro_winkler_similarity() with first-letter + double-metaphone blocking.

    Replaces the ProcessPoolExecutor parallel precompute.  The cache structure
    produced is identical to _score_indiv_keys_batch / _score_entity_keys_batch
    so the Phase 1 fan-out code is completely unchanged.

    NoMatch rows only include within-block pairs that failed the JW threshold
    (near-misses), not every possible pair below threshold.  This reduces
    NoMatch table writes by ~100-500x for typical data.

    Blocking strategy
    -----------------
    Individuals (Pass 1 & 2):
        UNION of two hash-join blocks:
          Block A  –  sdn.fn1=inp.fn1 AND sdn.ln1=inp.ln1  (same first letters)
          Block B  –  sdn.fn_dm=inp.fn_dm AND sdn.ln_dm=inp.ln_dm  (same DM code)
        Plus length filter: |fn_len_diff| <= 3 and |ln_len_diff| <= 3
        Together these capture essentially all pairs where JW >= 75% while
        examining < 1% of the full cross-product.

    Entities (Pass 3 & 3b):
        Block on first 2 characters of the normalised entity name.
        Plus length filter: |len_diff| <= max(6, inp_len // 3)
    """
    con = _duckdb.connect()

    # ------------------------------------------------------------------
    # Helper: first double-metaphone code (or soundex as fallback)
    # ------------------------------------------------------------------
    def _first_dm(nm: str) -> str:
        if not nm:
            return ''
        if _DM_AVAILABLE:
            codes = _dm_codes(nm)
            return next(iter(codes)) if codes else ''
        return _soundex(nm)

    JW_NM_THR = jw_name_pct           # 0-100 scale (threshold for Pass 1/2)

    # ==================================================================
    # PASS 1 & 2 — Individual name matching
    # ==================================================================
    if unique_indiv_keys:
        uid_to_fn = name_idx['uid_to_fn_nm']
        uid_to_ln = name_idx['uid_to_ln_nm']

        # ---- Build input table ----------------------------------------
        inp_rows: list = []
        for fn_nm, mn_nm, ln_nm in unique_indiv_keys:
            inp_rows.append((
                fn_nm, mn_nm, ln_nm,
                fn_nm[:1] if fn_nm else '',
                ln_nm[:1] if ln_nm else '',
                len(fn_nm), len(ln_nm),
                _first_dm(fn_nm), _first_dm(ln_nm),
            ))

        # ---- Build SDN individual table ---------------------------------
        sdn_rows: list = []
        for uid, (fn_raw, ln_raw, sdt) in sdn_entry_map.items():
            fn_nm_s = uid_to_fn.get(uid, '')
            ln_nm_s = uid_to_ln.get(uid, '')
            sdn_rows.append((
                uid,
                fn_raw or '', ln_raw or '', sdt or '',
                fn_nm_s, ln_nm_s,
                fn_nm_s[:1] if fn_nm_s else '',
                ln_nm_s[:1] if ln_nm_s else '',
                len(fn_nm_s), len(ln_nm_s),
                _first_dm(fn_nm_s), _first_dm(ln_nm_s),
            ))

        # ---- Build SDN AKA table ----------------------------------------
        # Pre-normalise AKA names here so DuckDB sees the same strings
        # that the original _score_aka() would compute.
        aka_rows: list = []
        for suid, akas in aka_by_sdn.items():
            for auid, afn, aln, acat in akas:
                afn_nm = _ph_norm_name(normalize(afn or '', strip_pat))
                aln_nm = _ph_norm_name(normalize(aln or '', strip_pat))
                aka_rows.append((
                    suid, auid, acat or '',
                    afn or '', aln or '',    # raw (for storage)
                    afn_nm, aln_nm,          # normalised (for JW)
                    afn_nm[:1] if afn_nm else '',
                    aln_nm[:1] if aln_nm else '',
                    len(afn_nm), len(aln_nm),
                    _first_dm(afn_nm), _first_dm(aln_nm),
                ))

        # ---- Load into DuckDB ------------------------------------------
        con.execute('''CREATE TABLE inp_indiv (
            fn_nm VARCHAR, mn_nm VARCHAR, ln_nm VARCHAR,
            fn1 VARCHAR, ln1 VARCHAR,
            fn_len INT, ln_len INT,
            fn_dm VARCHAR, ln_dm VARCHAR)''')
        con.execute('''CREATE TABLE sdn_indiv (
            uid INT, fn_raw VARCHAR, ln_raw VARCHAR, sdt VARCHAR,
            fn_nm VARCHAR, ln_nm VARCHAR,
            fn1 VARCHAR, ln1 VARCHAR,
            fn_len INT, ln_len INT,
            fn_dm VARCHAR, ln_dm VARCHAR)''')
        con.execute('''CREATE TABLE sdn_aka (
            suid INT, auid INT, acat VARCHAR,
            afn_raw VARCHAR, aln_raw VARCHAR,
            afn_nm VARCHAR, aln_nm VARCHAR,
            fn1 VARCHAR, ln1 VARCHAR,
            fn_len INT, ln_len INT,
            fn_dm VARCHAR, ln_dm VARCHAR)''')

        con.executemany('INSERT INTO inp_indiv VALUES (?,?,?,?,?,?,?,?,?)',   inp_rows)
        con.executemany('INSERT INTO sdn_indiv VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', sdn_rows)
        con.executemany('INSERT INTO sdn_aka   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)', aka_rows)

        # ---- Blocked JW query — Pass 1 (matches only) ------------------
        # Two hash-join blocks (first letter and DM code) UNION-ed so that
        # pairs in both blocks are evaluated only once.  The WHERE clause
        # filters to matches only — no NoMatch rows are collected.
        SQL_P1 = f'''
        WITH cands AS (
            SELECT i.fn_nm, i.mn_nm, i.ln_nm,
                   s.uid, s.fn_raw, s.ln_raw, s.sdt,
                   s.fn_nm AS s_fn, s.ln_nm AS s_ln
            FROM inp_indiv i JOIN sdn_indiv s
                ON s.fn1=i.fn1 AND s.ln1=i.ln1
                AND ABS(s.fn_len-i.fn_len)<=3 AND ABS(s.ln_len-i.ln_len)<=3
            UNION
            SELECT i.fn_nm, i.mn_nm, i.ln_nm,
                   s.uid, s.fn_raw, s.ln_raw, s.sdt,
                   s.fn_nm, s.ln_nm
            FROM inp_indiv i JOIN sdn_indiv s
                ON s.fn_dm=i.fn_dm AND s.ln_dm=i.ln_dm
                AND ABS(s.fn_len-i.fn_len)<=3 AND ABS(s.ln_len-i.ln_len)<=3
        )
        SELECT fn_nm, mn_nm, ln_nm, uid, fn_raw, ln_raw, sdt,
               ROUND(jaro_winkler_similarity(fn_nm, s_fn)*100, 2) AS fn_jw,
               ROUND(jaro_winkler_similarity(ln_nm, s_ln)*100, 2) AS ln_jw
        FROM cands
        WHERE jaro_winkler_similarity(fn_nm, s_fn)*100 >= {JW_NM_THR}
          AND jaro_winkler_similarity(ln_nm, s_ln)*100 >= {JW_NM_THR}
        '''
        p1_rows = con.execute(SQL_P1).fetchall()

        # ---- Blocked JW query — Pass 2 (AKA, matches only) ------------
        SQL_P2 = f'''
        WITH cands AS (
            SELECT i.fn_nm, i.mn_nm, i.ln_nm,
                   a.suid, a.auid, a.acat, a.afn_raw, a.aln_raw,
                   a.afn_nm, a.aln_nm
            FROM inp_indiv i JOIN sdn_aka a
                ON a.fn1=i.fn1 AND a.ln1=i.ln1
                AND ABS(a.fn_len-i.fn_len)<=3 AND ABS(a.ln_len-i.ln_len)<=3
            UNION
            SELECT i.fn_nm, i.mn_nm, i.ln_nm,
                   a.suid, a.auid, a.acat, a.afn_raw, a.aln_raw,
                   a.afn_nm, a.aln_nm
            FROM inp_indiv i JOIN sdn_aka a
                ON a.fn_dm=i.fn_dm AND a.ln_dm=i.ln_dm
                AND ABS(a.fn_len-i.fn_len)<=3 AND ABS(a.ln_len-i.ln_len)<=3
        )
        SELECT fn_nm, mn_nm, ln_nm,
               suid, auid, acat, afn_raw, aln_raw,
               ROUND(jaro_winkler_similarity(fn_nm, afn_nm)*100, 2) AS fn_jw,
               ROUND(jaro_winkler_similarity(ln_nm, aln_nm)*100, 2) AS ln_jw
        FROM cands
        WHERE jaro_winkler_similarity(fn_nm, afn_nm)*100 >= {JW_NM_THR}
          AND jaro_winkler_similarity(ln_nm, aln_nm)*100 >= {JW_NM_THR}
        '''
        p2_rows = con.execute(SQL_P2).fetchall()

        # ---- Initialise cache entries for every key --------------------
        for key in unique_indiv_keys:
            indiv_name_cache[key] = {'full': [], 'aka': [], 'has_full': False}

        # ---- Distribute Pass 1 rows into cache -------------------------
        for row in p1_rows:
            fn, mn, ln, uid, fn_raw, ln_raw, sdt, fn_jw, ln_jw = row
            key = (fn, mn, ln)
            entry = indiv_name_cache[key]
            entry['full'].append(
                (fn, mn, ln, uid, fn_raw, ln_raw, sdt, 'sdnEntry',
                 fn_jw, ln_jw, 1))
            entry['has_full'] = True

        # ---- Distribute Pass 2 rows into cache -------------------------
        for row in p2_rows:
            fn, mn, ln, suid, auid, acat, afn_raw, aln_raw, fn_jw, ln_jw = row
            key = (fn, mn, ln)
            entry = indiv_name_cache[key]
            entry['aka'].append(
                (suid, auid, acat,
                 fn, mn, ln,
                 afn_raw, aln_raw,
                 fn_jw, ln_jw, 1))
            entry['has_full'] = True

        con.execute('DROP TABLE inp_indiv; DROP TABLE sdn_indiv; DROP TABLE sdn_aka')

    # ==================================================================
    # PASS 3 & 3b — Entity org-name matching
    # ==================================================================
    if unique_entity_keys:

        # ---- Build input entity table ----------------------------------
        ent_inp_rows: list = []
        for entity_nm in unique_entity_keys:
            ent_inp_rows.append((
                entity_nm,
                entity_nm[:2] if len(entity_nm) >= 2 else entity_nm,
                len(entity_nm),
            ))

        # ---- Build SDN entity table (Pass 3) ---------------------------
        sdn_ent_rows: list = []
        for uid, (raw_nm, norm_nm) in entity_org_map.items():
            nm = norm_nm or ''
            sdn_ent_rows.append((
                uid, raw_nm or '', nm,
                nm[:2] if len(nm) >= 2 else nm,
                len(nm),
            ))

        # ---- Build SDN entity AKA table (Pass 3b) ----------------------
        sdn_ent_aka_rows: list = []
        for (suid, auid), (raw_nm, norm_nm, acat) in entity_aka_norm.items():
            nm = norm_nm or ''
            sdn_ent_aka_rows.append((
                suid, auid, acat or '',
                raw_nm or '', nm,
                nm[:2] if len(nm) >= 2 else nm,
                len(nm),
            ))

        # ---- Load into DuckDB ------------------------------------------
        con.execute('''CREATE TABLE inp_ent (
            entity_nm VARCHAR, en2 VARCHAR, en_len INT)''')
        con.execute('''CREATE TABLE sdn_ent (
            uid INT, raw_nm VARCHAR, norm_nm VARCHAR,
            en2 VARCHAR, en_len INT)''')
        con.execute('''CREATE TABLE sdn_ent_aka (
            suid INT, auid INT, acat VARCHAR,
            raw_nm VARCHAR, norm_nm VARCHAR,
            en2 VARCHAR, en_len INT)''')

        con.executemany('INSERT INTO inp_ent VALUES (?,?,?)',          ent_inp_rows)
        con.executemany('INSERT INTO sdn_ent VALUES (?,?,?,?,?)',      sdn_ent_rows)
        con.executemany('INSERT INTO sdn_ent_aka VALUES (?,?,?,?,?,?,?)', sdn_ent_aka_rows)

        # ---- Blocked JW query — Pass 3 (matches only) ------------------
        # Block on first 2 characters + length proximity (±max(6, 33% of len))
        # Note: no full-name JW threshold here — the Entity Suffix gate
        # (_entity_match_gate_v2) can accept pairs with full_jw below
        # ENTITY_GATE_JW when their Entity Suffix and other words match.
        # The en2 + length-proximity join is the blocking mechanism that
        # keeps the candidate set bounded.
        SQL_P3 = '''
        SELECT i.entity_nm, s.uid, s.raw_nm, s.norm_nm,
               ROUND(jaro_winkler_similarity(i.entity_nm, s.norm_nm)*100, 2) AS full_jw
        FROM inp_ent i JOIN sdn_ent s
            ON s.en2 = i.en2
            AND ABS(s.en_len - i.en_len) <= GREATEST(6, CAST(i.en_len AS INT) / 3)
        '''
        p3_rows = con.execute(SQL_P3).fetchall()

        # ---- Blocked JW query — Pass 3b (matches only) -----------------
        SQL_P3B = '''
        SELECT i.entity_nm, a.suid, a.auid, a.acat, a.raw_nm, a.norm_nm,
               ROUND(jaro_winkler_similarity(i.entity_nm, a.norm_nm)*100, 2) AS full_jw
        FROM inp_ent i JOIN sdn_ent_aka a
            ON a.en2 = i.en2
            AND ABS(a.en_len - i.en_len) <= GREATEST(6, CAST(i.en_len AS INT) / 3)
        '''
        p3b_rows = con.execute(SQL_P3B).fetchall()

        # ---- Initialise cache entries -----------------------------------
        for key in unique_entity_keys:
            entity_name_cache[key] = {'org': [], 'org_aka': [], 'has_full': False}

        # ---- Helper: word-level JW counts (for matched pairs only) -----
        def _word_counts(src_nm: str, sdn_nm: str) -> tuple:
            """Return (src_wc, sdn_wc, jw_m) for an already-matched pair."""
            src_words = src_nm.lower().split() if src_nm else []
            sdn_words = sdn_nm.lower().split() if sdn_nm else []
            if not run_org_word_match or not src_words or not sdn_words:
                return len(src_words), len(sdn_words), 0
            jw_m = sum(
                1 for sw in src_words
                if any(_jaro_winkler_fast(sw, dw) >= jw_org_threshold
                       for dw in sdn_words)
            )
            return len(src_words), len(sdn_words), jw_m

        # ---- Distribute Pass 3 rows into cache -------------------------
        for row in p3_rows:
            entity_nm, uid, raw_nm, norm_nm, full_jw = row
            src_wc, sdn_wc, jw_m = _word_counts(entity_nm, norm_nm)
            if not _entity_match_gate_v2(entity_nm, norm_nm, src_wc, sdn_wc, jw_m,
                                          full_jw, jw_org_threshold, entity_map):
                continue
            entry = entity_name_cache[entity_nm]
            entry['org'].append(
                (uid, raw_nm, norm_nm, src_wc, sdn_wc, jw_m, full_jw))
            entry['has_full'] = True

        # ---- Distribute Pass 3b rows into cache ------------------------
        for row in p3b_rows:
            entity_nm, suid, auid, acat, raw_nm, norm_nm, full_jw = row
            src_wc, sdn_wc, jw_m = _word_counts(entity_nm, norm_nm)
            if not _entity_match_gate_v2(entity_nm, norm_nm, src_wc, sdn_wc, jw_m,
                                          full_jw, jw_org_aka_threshold, entity_map):
                continue
            entry = entity_name_cache[entity_nm]
            entry['org_aka'].append(
                (suid, auid, acat, raw_nm, norm_nm,
                 src_wc, sdn_wc, jw_m, full_jw))
            entry['has_full'] = True

        con.execute('DROP TABLE inp_ent; DROP TABLE sdn_ent; DROP TABLE sdn_ent_aka')

    con.close()


def _conn_str(server: str, database: str) -> str:
    """Build an ODBC connection string.

    Defaults to Windows-integrated auth (Trusted_Connection=yes) for local
    SQL Server use. If SQL_USER / SQL_PASSWORD env vars are set (e.g. when
    running against Azure SQL Database from a container), SQL auth is used
    instead, with encryption enabled as Azure SQL requires.
    """
    import os
    user = os.environ.get('SQL_USER')
    pwd  = os.environ.get('SQL_PASSWORD')
    driver = os.environ.get('SQL_DRIVER', 'ODBC Driver 17 for SQL Server')
    if user and pwd:
        return (f"DRIVER={{{driver}}};SERVER={server};DATABASE={database};"
                f"UID={user};PWD={pwd};Encrypt=yes;TrustServerCertificate=no;"
                f"Connection Timeout=30;")
    return (f"DRIVER={{{driver}}};"
            f"SERVER={server};DATABASE={database};Trusted_Connection=yes;")


def _sdn_limit_type(v: str):
    if v.upper() == 'ALL':
        return None
    try:
        n = int(v)
        if n <= 0:
            raise argparse.ArgumentTypeError('--sdn-limit must be a positive integer or ALL')
        return n
    except ValueError:
        raise argparse.ArgumentTypeError('--sdn-limit must be a positive integer or ALL')


def main():
    ap = argparse.ArgumentParser(
        description="SDN matching v2 -- names + addresses, full-text + word-level"
    )
    # Input source (mutually exclusive)
    inp = ap.add_mutually_exclusive_group(required=True)
    inp.add_argument('--input-csv',        metavar='PATH',
                     help='Input CSV file (columns: ' + ', '.join(_INPUT_COLS) + ')')
    inp.add_argument('--input-table',      metavar='TABLE',
                     help='Input DB table or view (used with --input-server/--input-database)')
    inp.add_argument('--input-principals', action='store_true',
                     help='Load input from dbo.Principals_Alpha (uses --ca-server/--ca-database)')
    inp.add_argument('--input-screening', action='store_true',
                     help='Load input from a ScreeningInput-schema table in the SDN database (uses --sdn-server/--sdn-database/--sdn-schema/--screening-table)')
    ap.add_argument('--screening-table', default='ScreeningInput', metavar='TABLE',
                    help='Table name to read when using --input-screening (default: ScreeningInput)')
    ap.add_argument('--input-server',    default='.', metavar='SERVER')
    ap.add_argument('--input-database',  default='',  metavar='DATABASE')
    # California / Principals_Alpha connection
    ap.add_argument('--ca-server',    default='.',          metavar='SERVER',
                    help='SQL Server for Principals_Alpha (default: .)')
    ap.add_argument('--ca-database',  default='California', metavar='DATABASE',
                    help='Database for Principals_Alpha (default: California)')
    ap.add_argument('--entity-name',  default='*',          metavar='PREFIX',
                    help='Filter Principals_Alpha by ENTITY_NAME prefix; * = all (default)')
    ap.add_argument('--top-rows',     default=None, type=int, metavar='N',
                    help='Process only the first N rows from the input source')
    ap.add_argument('--start-row',    default=None, type=int, metavar='ID',
                    help='Start at this ScreeningInput_ID (inclusive); use with --top-rows to page through the table')
    # SDN / DW / output connections
    ap.add_argument('--sdn-server',      default='.')
    ap.add_argument('--sdn-database',    default='SDN')
    ap.add_argument('--sdn-schema',      default='dbo',
                    help='Schema for SDN input tables, e.g. ScreeningInput (default: dbo)')
    ap.add_argument('--drop-sdn-input',  action='store_true',
                    help='DROP and recreate ScreeningInput before run (destroys existing data)')
    ap.add_argument('--out-server',      default='.')
    ap.add_argument('--out-database',    default='SDNReporting')
    ap.add_argument('--out-schema',      default='dbo')
    ap.add_argument('--drop-output',     action='store_true')
    ap.add_argument('--no-csv',          action='store_true')
    ap.add_argument('--flush-interval',  type=int, default=50, metavar='N',
                    help='Flush output batches to SQL every N input records (default: 50)')
    ap.add_argument('--no-org-word-match', action='store_true',
                    help='Disable per-word scoring in Passes 3 and 3b. '
                         'Entity names are still compared full-string; '
                         'WordNumberMatching* columns will be 0.')
    ap.add_argument('--output',          default='MatchingResults_v2.csv')
    ap.add_argument('--config',          default='sdn_match_v2.cfg')
    ap.add_argument('--max-addr-candidates', type=int, default=50)
    ap.add_argument('--dummy-records',    type=int, default=0, metavar='N',
                    help='Append N synthetic records targeting 75-99%% JW similarity '
                         'across all pass types (distributed across Person, AKA Person, '
                         'Entity, Entity AKA, and Linked-to categories; ~30%% also '
                         'include a perturbed City+Country address)')
    ap.add_argument('--workers',          type=int,
                    default=multiprocessing.cpu_count(),
                    metavar='N',
                    help='Number of parallel worker processes for cache precompute '
                         f'(default: cpu_count = {multiprocessing.cpu_count()}). '
                         'Set to 1 to disable multiprocessing.  '
                         'Ignored when DuckDB blocking is active.')
    ap.add_argument('--no-duckdb',        action='store_true',
                    help='Disable DuckDB blocked JW precompute and fall back to '
                         'the parallel-worker approach (useful for debugging or '
                         'when duckdb is unavailable).')
    ap.add_argument('--sdn-limit',        type=_sdn_limit_type, default=None, metavar='N|ALL',
                    help='Limit SDN records evaluated to first N entries by uid '
                         '(e.g. --sdn-limit 500). Omit or use ALL for the full SDN list.')
    ap.add_argument('--input-source',     default=None, metavar='STR',
                    help='Override the input_source string recorded in MatchingResults_v2_RunLog '
                         '(e.g. "Delaware OFAC List — as of 2026-05-19 11:02:46 EST").')
    args = ap.parse_args()

    cfg              = load_v2_config(args.config)
    scores_cfg       = cfg['scores']
    strip_pat        = _build_strip_pattern(cfg['keep_chars'])
    use_phonetic         = cfg['use_phonetic']
    jw_name_threshold    = cfg['jw_name_threshold']
    jw_name_pct          = jw_name_threshold * 100   # scores stored as 0-100
    jw_org_threshold     = cfg['jw_org_threshold']
    jw_org_aka_threshold = cfg['jw_org_aka_threshold']
    min_jw_addr          = cfg['min_jw_addr']

    print(f"Org name thresholds: JW={jw_org_threshold:.0%}")
    print(f"Org AKA thresholds:  JW={jw_org_aka_threshold:.0%}")
    print(f"Address JW threshold: {min_jw_addr:.0%}")

    sdn_cs = _conn_str(args.sdn_server, args.sdn_database)
    out_cs = _conn_str(args.out_server,  args.out_database)

    # Resolve SDN publish info
    pub_id, pub_date = get_sdn_publish_info(sdn_cs)
    print(f"SDN publish info: ID={pub_id}  date={pub_date}")

    # Load SDN data + ensure ScreeningInput table exists in SDN database
    with pyodbc.connect(sdn_cs) as sdn_conn:
        setup_sdn_input_table(sdn_conn, args.sdn_schema, drop=args.drop_sdn_input)

        # Load abbreviation map (needed by load_sdn_addresses, so load first)
        print("Loading address abbreviation map...")
        abbrev_map = load_abbrev_map(sdn_conn)
        print(f"  {len(abbrev_map):,} abbreviations loaded.")

        # Load entity type map from the SDN database
        print("Loading entity type map...")
        entity_map = load_entity_type_map(sdn_conn)
        print(f"  {len(entity_map['suffix_phrases']) + len(entity_map['prefix_phrases'])} entity type phrases loaded.")

        name_idx = load_sdn_names(sdn_conn, strip_pat, sdn_limit=args.sdn_limit)
        addresses, addr_word_index = load_sdn_addresses(sdn_conn, abbrev_map, strip_pat,
                                                        sdn_limit=args.sdn_limit)
        (linked_to_by_uid, phones_by_uid,
         lt_word_index, phone_last7_idx) = load_sdn_remarks(sdn_conn, strip_pat,
                                                            sdn_limit=args.sdn_limit,
                                                            entity_map=entity_map)

    # Load input records
    if args.input_csv:
        input_records = load_input_csv(args.input_csv)
        input_source  = f"CSV:{args.input_csv}"
    elif args.input_principals:
        input_records = load_input_principals(args.ca_server, args.ca_database,
                                              args.entity_name, args.top_rows)
        input_source  = (f"DB:{args.ca_server}.{args.ca_database}.dbo.Principals_Alpha"
                         + (f"[{args.entity_name}%]" if args.entity_name != '*' else ''))
    elif args.input_screening:
        input_records = load_input_screening(args.sdn_server, args.sdn_database,
                                             args.sdn_schema, args.screening_table,
                                             args.top_rows, args.start_row)
        # Auto-derive label from Upload_Date written by import_screening_xlsx.py
        with pyodbc.connect(sdn_cs) as _c:
            _row = _c.cursor().execute(
                f"SELECT MAX(Upload_Date) FROM [{args.sdn_schema}].[{args.screening_table}]"
            ).fetchone()
        _as_of = _row[0] if (_row and _row[0]) else None
        if _as_of:
            input_source = (f"ScreeningInput as of {_as_of.strftime('%Y-%m-%d %H:%M:%S')}"
                            f" [{args.sdn_database}]")
        else:
            input_source = (f"DB:{args.sdn_server}.{args.sdn_database}"
                            f".[{args.sdn_schema}].[{args.screening_table}]")
    else:
        if not args.input_database:
            sys.exit("--input-database is required when using --input-table")
        input_records = load_input_db(args.input_server, args.input_database,
                                      args.input_table)
        input_source  = f"DB:{args.input_server}.{args.input_database}.{args.input_table}"

    if args.input_source:
        input_source = args.input_source

    # Normalise all input records up front
    print("Normalising input records...")
    for rec in input_records:
        normalize_input(rec, abbrev_map, strip_pat, entity_map)
    print(f"  {len(input_records):,} records normalised")

    # Setup output
    s = args.out_schema
    with pyodbc.connect(out_cs) as out_conn:
        setup_output_tables(out_conn, s, drop=args.drop_output)
        run_id = create_run(out_conn, s, input_source, pub_id, pub_date)
        print(f"Run ID: {run_id}")

    # Build full SDN coverage map: uid -> (fn_orig, ln_orig, sdntype)
    # Used to write zero rows for every SDN entry not found as a candidate,
    # proving each input was evaluated against the entire SDN list.
    all_sdn_entries  = name_idx['all_sdn_entries']           # list of (uid, fn, ln, sdt)

    # Pass 1 covers Individuals only.
    # Entity / Vessel entries have different comparison rules and are handled separately.
    sdn_entry_map    = {uid: (fn, ln, sdt)
                        for uid, fn, ln, sdt in all_sdn_entries
                        if sdt == 'Individual'}
    aka_by_sdn       = name_idx['aka_by_sdn']               # uid -> [(aka_uid, fn, ln, cat)]
    n_individuals    = len(sdn_entry_map)
    n_total_sdn      = len(all_sdn_entries)
    print(f"SDN coverage (Pass 1 — Individuals): {n_individuals:,} of {n_total_sdn:,} "
          f"sdnEntry records — one row per (input × Individual sdnEntry) will be written.")

    # Pass 3 & 3b — Entity org name comparison
    # Build entity org map and word pre-filter index from all_sdn_entries.
    # SDN entity org names are normalised here with the same strip_pat so that
    # word-level comparisons use the same punctuation rules as the input side.
    run_org_word_match = not args.no_org_word_match
    entity_org_map     = {}   # uid -> (raw_ln, norm_ln)
    for uid, fn, ln, sdt in all_sdn_entries:
        if sdt != 'Entity':
            continue
        raw_ln  = ln or ''
        norm_ln = _ph_norm_name(expand_entity_nm(raw_ln, strip_pat, entity_map))
        entity_org_map[uid] = (raw_ln if ln else None, norm_ln)
    n_entities = len(entity_org_map)
    print(f"SDN coverage (Pass 3 — Entities):    {n_entities:,} Entity sdnEntry records.")
    if not run_org_word_match:
        print("  Per-word scoring disabled (--no-org-word-match); "
              "WordNumberMatching* columns will be 0. WARNING: outside of "
              "the Entity Suffix gate (which computes its own per-word "
              "scoring independent of this flag), the fallback entity "
              "match gate requires abs(SourceNumberofWords - "
              "WordNumberMatchingJaroWinkler) in (0,1), so multi-word "
              "entity names without a matching Entity Suffix will fail to "
              "match while this flag is set.")

    # Pass 3b — Entity AKA org name comparison (completely independent of Pass 3)
    entity_aka_by_sdn = name_idx['entity_aka_by_sdn']  # sdn_uid -> [(aka_uid, ln, cat)]
    entity_aka_norm   = {}   # (sdn_uid, aka_uid) -> (raw_ln, norm_ln, category)
    for sdn_uid, akas in entity_aka_by_sdn.items():
        for aka_uid, ln, category in akas:
            raw_ln  = ln or ''
            norm_ln = _ph_norm_name(expand_entity_nm(raw_ln, strip_pat, entity_map))
            entity_aka_norm[(sdn_uid, aka_uid)] = (raw_ln if ln else None, norm_ln, category)
    n_entity_akas = len(entity_aka_norm)
    print(f"SDN coverage (Pass 3b — Entity AKAs): {n_entity_akas:,} Entity AKA entries.")

    # Inject synthetic dummy records if requested (--dummy-records N)
    if args.dummy_records > 0:
        print(f"Generating {args.dummy_records:,} dummy records "
              f"(JW target band 75-99%% across all pass types)...")
        dummy_recs = generate_dummy_records(
            n                = args.dummy_records,
            sdn_entry_map    = sdn_entry_map,
            aka_by_sdn       = aka_by_sdn,
            entity_org_map   = entity_org_map,
            entity_aka_norm  = entity_aka_norm,
            linked_to_by_uid = linked_to_by_uid,
            addresses        = addresses,
            strip_pat        = strip_pat,
        )
        for rec in dummy_recs:
            normalize_input(rec, abbrev_map, strip_pat, entity_map)
        n_with_addr = sum(1 for r in dummy_recs if r.city or r.country)
        # Persist to ScreeningInput so dummy rows appear alongside real data
        with pyodbc.connect(sdn_cs) as sdn_conn:
            insert_dummy_screening_rows(sdn_conn, args.sdn_schema, dummy_recs)
        input_records = input_records + dummy_recs
        print(f"  {len(dummy_recs):,} dummy records written to "
              f"[{args.sdn_database}].[{args.sdn_schema}].[ScreeningInput] "
              f"and appended to this run "
              f"({n_with_addr:,} include embedded city/country address).")

    # ---------------------------------------------------------------------------
    # Cache precompute — score every unique name/entity key against the full
    # SDN set.  Two strategies available:
    #
    #   DuckDB (default, fast): blocked JW using first-letter + double-metaphone
    #       hash joins.  Examines < 1% of the full cross-product, giving ~100x
    #       speedup.  NoMatch rows are limited to within-block near-misses only.
    #       Requires: pip install duckdb
    #
    #   Parallel workers (fallback or --no-duckdb): ProcessPoolExecutor batches
    #       the full cross-product across CPU cores.  Use --workers N to tune.
    #
    # Any cache-miss keys (e.g. dummy records added after this step) are caught
    # by the existing guard in the Phase 1 fan-out loop.
    # ---------------------------------------------------------------------------
    _rf_str = 'rapidfuzz C++' if _RAPIDFUZZ else 'pure-Python'

    unique_indiv_keys  = list({
        (r.first_name_nm, r.middle_name_nm, r.last_name_nm)
        for r in input_records if r.entity_type != 'Entity'
    })
    unique_entity_keys = list({
        r.entity_name_nm
        for r in input_records
        if r.entity_type != 'Individual' and r.entity_name_nm
    })

    # These dicts are declared here (not inside the with-block below) so the
    # pre-computed entries survive into the fan-out loop.
    indiv_name_cache:  dict = {}
    entity_name_cache: dict = {}

    n_indiv_keys  = len(unique_indiv_keys)
    n_entity_keys = len(unique_entity_keys)
    print(f"Cache precompute: {n_indiv_keys:,} unique individual keys, "
          f"{n_entity_keys:,} unique entity keys ...")

    _use_duckdb = _DUCKDB and not args.no_duckdb
    if _use_duckdb:
        _dm_str = 'DM' if _DM_AVAILABLE else 'Soundex'
        print(f"  Strategy: DuckDB blocked JW (letter + {_dm_str} blocks)  "
              f"|  JW engine: {_rf_str}")
        _t0_pre = time.perf_counter()
        _precompute_duckdb(
            indiv_name_cache, entity_name_cache,
            unique_indiv_keys, unique_entity_keys,
            name_idx, aka_by_sdn, sdn_entry_map,
            entity_org_map, entity_aka_norm,
            jw_name_pct, jw_org_threshold, jw_org_aka_threshold,
            run_org_word_match, strip_pat, entity_map,
        )
        _t1_pre = time.perf_counter()
        print(f"  {n_indiv_keys + n_entity_keys:,} keys precomputed "
              f"via DuckDB blocking in {_t1_pre - _t0_pre:.1f}s.")
    else:
        # -------------------------------------------------------------------
        # Fallback: parallel workers (ProcessPoolExecutor)
        # -------------------------------------------------------------------
        n_workers = max(1, args.workers)
        print(f"  Strategy: parallel workers ({n_workers})  "
              f"|  JW engine: {_rf_str}")

        _init_args = (sdn_entry_map, name_idx, aka_by_sdn,
                      entity_org_map, entity_aka_norm,
                      jw_name_pct, jw_org_threshold, jw_org_aka_threshold,
                      run_org_word_match, strip_pat, entity_map)

        def _chunk(lst: list, n: int) -> list:
            k = max(1, math.ceil(len(lst) / n))
            return [lst[i:i + k] for i in range(0, len(lst), k)]

        if n_workers > 1 and (unique_indiv_keys or unique_entity_keys):
            indiv_batches  = _chunk(unique_indiv_keys,  n_workers)
            entity_batches = _chunk(unique_entity_keys, n_workers)
            with ProcessPoolExecutor(max_workers=n_workers,
                                     initializer=_worker_init,
                                     initargs=_init_args) as _pool:
                _futs = (
                    [(_pool.submit(_score_indiv_keys_batch,  b), 'indiv')
                     for b in indiv_batches]
                  + [(_pool.submit(_score_entity_keys_batch, b), 'entity')
                     for b in entity_batches]
                )
                _n_done = 0
                _n_total = n_indiv_keys + n_entity_keys
                for _fut, _kind in _futs:
                    _br = _fut.result()
                    if _kind == 'indiv':
                        indiv_name_cache.update(_br)
                    else:
                        entity_name_cache.update(_br)
                    _n_done += len(_br)
                    print(f"  {_n_done:,}/{_n_total:,} keys precomputed ...", end='\r')
            print(f"  {_n_done:,} keys precomputed across {n_workers} workers.    ")
        else:
            _worker_init(*_init_args)
            if unique_indiv_keys:
                indiv_name_cache.update(_score_indiv_keys_batch(unique_indiv_keys))
            if unique_entity_keys:
                entity_name_cache.update(_score_entity_keys_batch(unique_entity_keys))
            print(f"  {n_indiv_keys + n_entity_keys:,} keys precomputed (1 worker).")

    total_full_rows      = 0
    total_aka_rows       = 0
    total_org_rows       = 0
    total_org_aka_rows   = 0
    total_addr_rows      = 0
    total_linked_to_rows = 0
    total_phone_rows     = 0
    total_no_match_rows  = 0   # input records with no match in any pass
    processed            = 0
    n_input              = len(input_records)

    # -----------------------------------------------------------------------
    # Phase 1 — all non-address passes for every input record.
    # Collects matched_input_pairs: (input_record_id, rec) for every record
    # that appears in at least one Full/match table (Person, AKA, OrgName,
    # OrgName_AKA, LinkedTo, Phone).  Phase 2 then runs address comparison
    # only for those records.
    # -----------------------------------------------------------------------
    print("Phase 1 — name / entity / linked-to / phone comparison ...")
    with pyodbc.connect(out_cs) as out_conn:
        full_batch:      list = []
        aka_batch:       list = []
        org_batch:       list = []
        org_aka_batch:   list = []
        linked_to_batch: list = []
        phone_batch:     list = []
        unmatched_records: list = []   # (input_record_id, rec) for records with no match

        # Scoring caches for Passes 5 & 6 (Passes 1-3b pre-populated above).
        # indiv_name_cache and entity_name_cache are declared and pre-populated
        # before this block by the parallel precompute step; do NOT re-declare them.
        linked_to_indiv_cache:  dict = {}
        linked_to_entity_cache: dict = {}
        phone_cache:            dict = {}

        # Records that had at least one Full match in Passes 1-3b, 5, or 6.
        # Phase 2 runs address comparison exclusively for these records.
        matched_input_pairs: list = []

        for input_record_id, rec in enumerate(input_records, 1):
            fn_nm = rec.first_name_nm
            mn_nm = rec.middle_name_nm
            ln_nm = rec.last_name_nm

            # ---------------------------------------------------------------
            # Passes 1 & 2 — Individual name matching (cache on normalized
            # name triple so identical names are scored only once).
            # Cache key : (fn_nm, mn_nm, ln_nm)
            # Cache value: dict with keys 'n_cands', 'full', 'nm',
            #              'aka', 'aka_nm' — SDN-side tuples only;
            #              per-record fields are spliced in at fan-out.
            # ---------------------------------------------------------------
            _name_full = False   # True if Pass 1/2 produced a Full match for this record
            if rec.entity_type != 'Entity':
                indiv_key = (fn_nm, mn_nm, ln_nm)

                if indiv_key not in indiv_name_cache:
                    # Pass 1 — score ALL SDN Individuals; FN+LN JW only
                    _p1_full: list = []
                    _p1_nm:   list = []
                    for _uid, (_sdn_fn, _sdn_ln, _sdn_type) in sdn_entry_map.items():
                        _sc = score_name_candidate(fn_nm, ln_nm, _uid, name_idx)
                        # _sc = [fn_jw, ln_jw] (0-100 pct)
                        if _sc[0] >= jw_name_pct and _sc[1] >= jw_name_pct:
                            _p1_full.append(
                                (fn_nm, mn_nm, ln_nm,
                                 _uid, _sdn_fn, _sdn_ln, _sdn_type, 'sdnEntry')
                                + tuple(_sc) + (1,))   # Personal_Name_Match=1
                        else:
                            # uid + fn_jw + ln_jw
                            _p1_nm.append((_uid,) + tuple(_sc))

                    # Pass 2 — score ALL Individual AKAs; FN+LN JW only
                    _p2_aka:    list = []
                    _p2_aka_nm: list = []
                    for _suid, _akas in aka_by_sdn.items():
                        for _auid, _afn, _aln, _acat in _akas:
                            _asc = _score_aka(
                                fn_nm, ln_nm,
                                _afn or '', _aln or '', strip_pat)
                            if _asc[0] >= jw_name_pct and _asc[1] >= jw_name_pct:
                                _p2_aka.append(
                                    (_suid, _auid, _s(_acat),
                                     fn_nm, mn_nm, ln_nm,
                                     _s(_afn), _s(_aln))
                                    + tuple(_asc) + (1,))
                            else:
                                # sdn_uid, aka_fn, aka_ln, fn_jw, ln_jw
                                _p2_aka_nm.append(
                                    (_suid, _s(_afn), _s(_aln)) + tuple(_asc))

                    indiv_name_cache[indiv_key] = {
                        'full':     _p1_full,
                        'aka':      _p2_aka,
                        'has_full': bool(_p1_full) or bool(_p2_aka),
                    }

                _ic = indiv_name_cache[indiv_key]
                _name_full = _ic['has_full']

                # Fan out Pass 1
                _fn_raw = _s(rec.first_name)
                _mn_raw = _s(rec.middle_name)
                _ln_raw = _s(rec.last_name)
                _p1_prefix = (run_id, input_record_id, pub_date,
                              _fn_raw, _mn_raw, _ln_raw)
                for _row in _ic['full']:
                    full_batch.append(_p1_prefix + _row)

                # Fan out Pass 2
                for _row in _ic['aka']:
                    aka_batch.append(
                        (run_id, input_record_id, _row[0],
                         _row[1], _row[2],
                         _fn_raw, _mn_raw, _ln_raw)
                        + _row[3:])

            # ---------------------------------------------------------------
            # Passes 3 & 3b — Entity org-name matching (cache on normalized
            # entity name so identical org names are scored only once).
            # Cache key : entity_name_nm
            # Cache value: dict with keys 'org', 'org_nm', 'org_aka',
            #              'org_aka_nm' — SDN-side tuples only.
            # Skipped entirely when --no-org-match is set.
            # ---------------------------------------------------------------
            src_org     = rec.entity_name_nm if rec.entity_type != 'Individual' else ''
            src_org_raw = _s(rec.entity_name) if rec.entity_type != 'Individual' else ''

            _org_full = False    # True if Pass 3/3b produced a Full match for this record
            if src_org:
                entity_key = src_org   # normalized form is the cache key

                if entity_key not in entity_name_cache:
                    # Pass 3 — Entity sdnEntry full cross-comparison; JW threshold
                    _p3_org: list = []
                    for _uid, (_sdn_raw, _sdn_nm) in entity_org_map.items():
                        _sc = score_org_name(
                            src_org, _sdn_nm,
                            jw_org_threshold,
                            run_org_word_match,
                            entity_map)
                        if _entity_match_gate_v2(src_org, _sdn_nm, *_sc, jw_org_threshold,
                                                 entity_map):
                            _p3_org.append((_uid, _sdn_raw, _sdn_nm) + tuple(_sc))

                    # Pass 3b — Entity AKA full cross-comparison
                    _p3b_aka: list = []
                    for (_suid, _auid), (_araw, _anm, _acat) in \
                            entity_aka_norm.items():
                        _sc = score_org_name(
                            src_org, _anm,
                            jw_org_aka_threshold,
                            run_org_word_match,
                            entity_map)
                        if _entity_match_gate_v2(src_org, _anm, *_sc, jw_org_aka_threshold,
                                                 entity_map):
                            _p3b_aka.append(
                                (_suid, _auid, _s(_acat), _araw, _anm)
                                + tuple(_sc))

                    entity_name_cache[entity_key] = {
                        'org':      _p3_org,
                        'org_aka':  _p3b_aka,
                        'has_full': bool(_p3_org) or bool(_p3b_aka),
                    }

                _ec = entity_name_cache[entity_key]
                _org_full = _ec['has_full']

                # Fan out Pass 3
                for _row in _ec['org']:
                    # row = (uid, sdn_raw, sdn_nm, src_wc, sdn_wc, jw_m, full_jw)
                    org_batch.append((
                        run_id, input_record_id, pub_date,
                        src_org_raw, src_org,
                        _row[0], _row[1], _row[2], 'Entity', 'sdnEntry',
                        *_row[3:],
                    ))

                # Fan out Pass 3b
                for _row in _ec['org_aka']:
                    # row = (sdn_uid, aka_uid, aka_cat, aka_raw, aka_nm,
                    #         src_wc, sdn_wc, jw_m, full_jw)
                    org_aka_batch.append((
                        run_id, input_record_id, pub_date,
                        src_org_raw, src_org,
                        _row[0], _row[1], _row[2],
                        _row[3], _row[4], 'Entity', 'akaList',
                        *_row[5:],
                    ))

            # ---------------------------------------------------------------
            # Pass 5 — Linked-to matching
            # Compare input name against "Linked to:" strings in SDN remarks.
            # Cache key: (fn_nm, mn_nm, ln_nm) for Individuals;
            #            entity_name_nm          for Entities.
            # Per-record SourceName (raw) is spliced in at fan-out time.
            # ---------------------------------------------------------------
            _lt_full = False     # True if Pass 5 produced a Full match for this record
            if rec.entity_type != 'Entity':
                # Individual: compare in both name orderings
                lt_indiv_key = (fn_nm, mn_nm, ln_nm)

                if lt_indiv_key not in linked_to_indiv_cache:
                    # Full cross-comparison against all Linked-to UIDs.
                    _lt_full_rows: list = []
                    _fn_mn_ln = re.sub(r'\s+', ' ',
                                       (fn_nm + ' ' + mn_nm + ' ' + ln_nm).strip())
                    _ln_fn_mn = re.sub(r'\s+', ' ',
                                       (ln_nm + ' ' + fn_nm + ' ' + mn_nm).strip())
                    _jw_lt_pct = jw_org_threshold * 100

                    for _uid, _occ_list in linked_to_by_uid.items():
                        for _occ, _lt_raw, _lt_nm, _lt_nm_exp in _occ_list:
                            for _src_nm_norm, _form in [
                                (_fn_mn_ln, 'FN_MN_LN'),
                                (_ln_fn_mn, 'LN_FN_MN'),
                            ]:
                                if not _src_nm_norm:
                                    continue
                                _src_wc, _lt_wc, _jw_wm, _jw = score_org_name(
                                    _src_nm_norm, _lt_nm,
                                    jw_org_threshold, word_match=True)
                                if _jw >= _jw_lt_pct:
                                    _lt_full_rows.append(
                                        (_uid, _occ, _lt_raw, _lt_nm, _lt_nm_exp,
                                         _src_nm_norm, _form, _src_wc, _lt_wc, _jw_wm, _jw)
                                    )
                    linked_to_indiv_cache[lt_indiv_key] = _lt_full_rows

                # Fan out — splice in per-record raw source name
                _fn_raw = _s(rec.first_name)  or ''
                _mn_raw = _s(rec.middle_name) or ''
                _ln_raw = _s(rec.last_name)   or ''
                _lt_match_rows = linked_to_indiv_cache[lt_indiv_key]
                _lt_full = bool(_lt_match_rows)
                for _row in _lt_match_rows:
                    _uid, _occ, _lt_raw, _lt_nm, _lt_nm_exp, _src_nm_norm, _form, _src_wc, _lt_wc, _jw_wm, _jw = _row
                    if _form == 'FN_MN_LN':
                        _src_raw = ' '.join(filter(None, [_fn_raw, _mn_raw, _ln_raw]))
                    else:
                        _src_raw = ' '.join(filter(None, [_ln_raw, _fn_raw, _mn_raw]))
                    linked_to_batch.append((
                        run_id, input_record_id, _uid, pub_date, _occ,
                        _lt_raw, _lt_nm, _lt_nm_exp, _src_raw, _src_nm_norm, _form,
                        _src_wc, _lt_wc, _jw_wm, _jw,
                    ))
            else:
                # Entity: compare entity_name_nm (suffix-expanded) against the
                # entity-suffix-expanded form of the Linked-to text, so both
                # sides of the comparison are normalized consistently.
                lt_entity_key = rec.entity_name_nm

                if lt_entity_key not in linked_to_entity_cache:
                    # Full cross-comparison against all Linked-to UIDs.
                    _lt_full_rows_e: list = []

                    for _uid, _occ_list in linked_to_by_uid.items():
                        for _occ, _lt_raw, _lt_nm, _lt_nm_exp in _occ_list:
                            _src_wc, _lt_wc, _jw_wm, _jw = score_org_name(
                                lt_entity_key, _lt_nm_exp,
                                jw_org_threshold, word_match=True,
                                entity_map=entity_map)
                            if _entity_match_gate_v2(lt_entity_key, _lt_nm_exp,
                                                      _src_wc, _lt_wc, _jw_wm, _jw,
                                                      jw_org_threshold, entity_map):
                                _lt_full_rows_e.append(
                                    (_uid, _occ, _lt_raw, _lt_nm, _lt_nm_exp,
                                     lt_entity_key, 'EntityName', _src_wc, _lt_wc, _jw_wm, _jw)
                                )
                    linked_to_entity_cache[lt_entity_key] = _lt_full_rows_e

                # Fan out — SourceName (raw) from record; SourceName_NM from cache
                _ent_raw = _s(rec.entity_name) or ''
                _lt_match_rows_e = linked_to_entity_cache[lt_entity_key]
                _lt_full = bool(_lt_match_rows_e)
                for _row in _lt_match_rows_e:
                    _uid, _occ, _lt_raw, _lt_nm, _lt_nm_exp, _src_nm_norm, _form, _src_wc, _lt_wc, _jw_wm, _jw = _row
                    linked_to_batch.append((
                        run_id, input_record_id, _uid, pub_date, _occ,
                        _lt_raw, _lt_nm, _lt_nm_exp, _ent_raw, _src_nm_norm, _form,
                        _src_wc, _lt_wc, _jw_wm, _jw,
                    ))

            # ---------------------------------------------------------------
            # Pass 6 — Phone matching
            # Candidate pre-filter: last-7 digits must match.
            # Scores: exact digit match, last-10 digit match, JW on digit strings.
            # Cache key: rec.phone_nm (digits-only string).
            # Run BEFORE Pass 4 (address) so a phone match can also count
            # toward the "referenced in a match table" gate for addresses.
            # ---------------------------------------------------------------
            _phone_full = False  # True if Pass 6 found any phone candidate match
            if rec.phone_nm:
                ph_key = rec.phone_nm

                if ph_key not in phone_cache:
                    _ph_rows: list = []
                    _last7 = rec.phone_nm[-7:]
                    for _uid in phone_last7_idx.get(_last7, set()):
                        for _sdn_raw, _sdn_digits in phones_by_uid.get(_uid, []):
                            _exact  = 1 if rec.phone_nm == _sdn_digits else 0
                            _last10 = (1 if len(rec.phone_nm) >= 10
                                            and len(_sdn_digits) >= 10
                                            and rec.phone_nm[-10:] == _sdn_digits[-10:]
                                       else 0)
                            _jw = _field_scores(rec.phone_nm, _sdn_digits)
                            _ph_rows.append(
                                (_uid, _sdn_raw, _sdn_digits, _exact, _last10, _jw))
                    phone_cache[ph_key] = _ph_rows

                _phone_full = bool(phone_cache[ph_key])
                for _row in phone_cache[ph_key]:
                    phone_batch.append((
                        run_id, input_record_id, _row[0], pub_date,
                        _row[1], _row[2],           # sdn_raw, sdn_digits
                        _s(rec.phone), rec.phone_nm,  # input raw, input digits
                        _row[3], _row[4], _row[5],    # exact, last10, jw
                    ))

            # Track whether this record matched anything in Phase 1
            if _name_full or _org_full or _lt_full or _phone_full:
                matched_input_pairs.append((input_record_id, rec))
            else:
                unmatched_records.append((input_record_id, rec))

            processed += 1
            if processed % args.flush_interval == 0:
                flush_full_results(out_conn, s, full_batch)
                flush_aka_results(out_conn, s, aka_batch)
                flush_org_results(out_conn, s, org_batch)
                flush_org_aka_results(out_conn, s, org_aka_batch)
                flush_linked_to_results(out_conn, s, linked_to_batch)
                flush_phone_results(out_conn, s, phone_batch)
                total_full_rows      += len(full_batch)
                total_aka_rows       += len(aka_batch)
                total_org_rows       += len(org_batch)
                total_org_aka_rows   += len(org_aka_batch)
                total_linked_to_rows += len(linked_to_batch)
                total_phone_rows     += len(phone_batch)
                full_batch      = []
                aka_batch       = []
                org_batch       = []
                org_aka_batch   = []
                linked_to_batch = []
                phone_batch     = []
                print(f"  Phase 1: {processed:,} / {n_input:,}  "
                      f"{total_full_rows:,} match  "
                      f"{total_aka_rows:,} AKA  "
                      f"{total_org_rows:,} org  "
                      f"{total_org_aka_rows:,} org-aka  "
                      f"{total_linked_to_rows:,} lnkd  "
                      f"{total_phone_rows:,} ph  "
                      f"| {len(matched_input_pairs):,} matched  "
                      f"{len(unmatched_records):,} no-match",
                      end='\r')

        # -----------------------------------------------------------------------
        # Phase 1 final flush
        # -----------------------------------------------------------------------
        flush_full_results(out_conn, s, full_batch)
        flush_aka_results(out_conn, s, aka_batch)
        flush_org_results(out_conn, s, org_batch)
        flush_org_aka_results(out_conn, s, org_aka_batch)
        flush_linked_to_results(out_conn, s, linked_to_batch)
        flush_phone_results(out_conn, s, phone_batch)
        total_full_rows      += len(full_batch)
        total_aka_rows       += len(aka_batch)
        total_org_rows       += len(org_batch)
        total_org_aka_rows   += len(org_aka_batch)
        total_linked_to_rows += len(linked_to_batch)
        total_phone_rows     += len(phone_batch)

        # -----------------------------------------------------------------------
        # Write one "No Match Found" row for every input record that had no
        # full match in any Phase 1 pass.
        # -----------------------------------------------------------------------
        _nm_log_batch = []
        for _iid, _rec in unmatched_records:
            _nm_log_batch.append((
                run_id, _iid,
                _s(_rec.first_name),
                _s(_rec.middle_name),
                _s(_rec.last_name),
                _s(_rec.entity_name),
                _s(_rec.external_id),
                'No Match Found',
            ))
        flush_no_match_log(out_conn, s, _nm_log_batch)
        total_no_match_rows = len(_nm_log_batch)

        n_matched = len(matched_input_pairs)
        print(f"\nPhase 1 complete: {n_input:,} input records  |  "
              f"{n_matched:,} matched ({n_matched/max(n_input,1):.1%})  |  "
              f"{total_no_match_rows:,} unmatched → MatchingResults_NoMatch")

        # -----------------------------------------------------------------------
        # Phase 2 — address comparison for matched records only.
        # Each record here already has an input_record_id assigned in Phase 1,
        # so address rows correctly join back to all other result tables.
        # Geo field pair caches persist across the full Phase 2 run.
        # -----------------------------------------------------------------------
        addr_batch:       list = []
        addr_score_cache: dict = {}
        city_geo_cache:     dict = {}   # (inp_city_nm,    sdn_city_nm)    -> jw
        region_geo_cache:   dict = {}   # (inp_region_nm,  sdn_region_nm)  -> jw
        postal_geo_cache:   dict = {}   # (inp_postal_nm,  sdn_postal_nm)  -> jw
        country_geo_cache:  dict = {}   # (inp_country_nm, sdn_country_nm) -> jw

        processed2 = 0
        for input_record_id, rec in matched_input_pairs:
            inp_mailing = ' '.join(filter(None, [rec.address1_nm,
                                                  rec.address2_nm,
                                                  rec.address3_nm]))
            addr_key = (rec.address1_nm, rec.address2_nm, rec.address3_nm,
                        rec.city_nm, rec.region_nm, rec.postal_code_nm, rec.country_nm)

            if inp_mailing or rec.city_nm or rec.region_nm or rec.postal_code_nm or rec.country_nm:
                if addr_key not in addr_score_cache:
                    # Full cross-comparison: score ALL SDN addresses.
                    # Full: any component except State/Region meets JW threshold.
                    _a_full_rows: list = []
                    _min_jw_pct = min_jw_addr * 100
                    for _sa in addresses:
                        jw_id = _find_street_jw_match(inp_mailing, _sa, min_jw_addr)

                        _cck = (rec.city_nm, _sa.city_nm)
                        city_jw = city_geo_cache.get(_cck)
                        if city_jw is None:
                            city_jw = _geo_addr_score(rec.city_nm, _sa.city_nm)
                            city_geo_cache[_cck] = city_jw

                        _rk = (rec.region_nm, _sa.state_province_nm)
                        state_jw = region_geo_cache.get(_rk)
                        if state_jw is None:
                            state_jw = _geo_addr_score(rec.region_nm, _sa.state_province_nm)
                            region_geo_cache[_rk] = state_jw

                        _pk = (rec.postal_code_nm, _sa.postal_code_nm)
                        postal_jw = postal_geo_cache.get(_pk)
                        if postal_jw is None:
                            postal_jw = _geo_addr_score(rec.postal_code_nm, _sa.postal_code_nm)
                            postal_geo_cache[_pk] = postal_jw

                        _cnk = (rec.country_nm, _sa.country_nm)
                        cntry_jw = country_geo_cache.get(_cnk)
                        if cntry_jw is None:
                            cntry_jw = _geo_addr_score(rec.country_nm, _sa.country_nm)
                            country_geo_cache[_cnk] = cntry_jw

                        if (jw_id != 32
                                or city_jw   >= _min_jw_pct
                                or postal_jw >= _min_jw_pct
                                or cntry_jw  >= _min_jw_pct):
                            _a_full_rows.append((
                                _sa.sdn_entry_uid, _sa.sdn_addr_uid,
                                _sa.address1, _sa.address1_nm,
                                _sa.address2, _sa.address2_nm,
                                _sa.address3, _sa.address3_nm,
                                _sa.city,           _sa.city_nm,
                                _sa.state_province, _sa.state_province_nm,
                                _sa.postal_code,    _sa.postal_code_nm,
                                _sa.country,        _sa.country_nm,
                                jw_id,
                                city_jw, state_jw, postal_jw, cntry_jw,
                            ))
                    addr_score_cache[addr_key] = _a_full_rows

                # Fan out — Full rows to addr_batch.
                # _sdn = (sdn_entry_uid, sdn_addr_uid,
                #          sdn_a1, sdn_a1_nm, sdn_a2, sdn_a2_nm, sdn_a3, sdn_a3_nm,
                #          sdn_city, sdn_city_nm, sdn_state, sdn_state_nm,
                #          sdn_postal, sdn_postal_nm, sdn_country, sdn_country_nm,
                #          jw_id,
                #          city_jw, state_jw, postal_jw, cntry_jw)  — 21 values
                for _sdn in addr_score_cache[addr_key]:
                    addr_batch.append((
                        run_id, input_record_id,
                        _sdn[0], _sdn[1],            # sdn_entry_uid, sdn_addr_uid
                        _s(rec.external_id),
                        _s(rec.address1),   rec.address1_nm,
                        _s(rec.address2),   rec.address2_nm,
                        _s(rec.address3),   rec.address3_nm,
                        _s(rec.city),       rec.city_nm,
                        _s(rec.region),     rec.region_nm,
                        _s(rec.postal_code), rec.postal_code_nm,
                        _s(rec.country),    rec.country_nm,
                        *_sdn[2:],                   # all 19 remaining SDN+score fields
                    ))

            processed2 += 1
            if processed2 % args.flush_interval == 0:
                flush_addr_full_results(out_conn, s, addr_batch)
                total_addr_rows += len(addr_batch)
                addr_batch = []
                print(f"  Phase 2: {processed2:,} / {n_matched:,}  "
                      f"{total_addr_rows:,} addr",
                      end='\r')

        # Phase 2 final flush
        flush_addr_full_results(out_conn, s, addr_batch)
        total_addr_rows += len(addr_batch)

    total_rows = (total_full_rows
                  + total_aka_rows
                  + total_org_rows
                  + total_org_aka_rows
                  + total_addr_rows
                  + total_linked_to_rows
                  + total_phone_rows
                  + total_no_match_rows)
    with pyodbc.connect(out_cs) as out_conn:
        update_run(out_conn, s, run_id, len(input_records), total_rows, 0, 0)
        person_summary_rows, org_summary_rows = populate_matching_summary(out_conn, s, run_id)

    print(f"\nDone.  run_id={run_id}")
    print(f"  {len(input_records):,} input records")
    print(f"  {total_full_rows:,} rows → MatchingResults_Person_Full")
    print(f"  {total_aka_rows:,} rows → MatchingResults_AKA")
    print(f"  {total_org_rows:,} rows → MatchingResults_OrgName")
    print(f"  {total_org_aka_rows:,} rows → MatchingResults_OrgName_AKA")
    print(f"  {total_addr_rows:,} rows → MatchingResults_Address")
    print(f"  {total_linked_to_rows:,} rows → MatchingResults_LinkedTo")
    print(f"  {total_phone_rows:,} rows → MatchingResults_Phone")
    print(f"  {total_no_match_rows:,} rows → MatchingResults_NoMatch  (input records with no match)")
    print(f"  {person_summary_rows:,} rows → Matching_Summary_Person")
    print(f"  {org_summary_rows:,} rows → Matching_Summary_Org")


if __name__ == '__main__':
    main()
