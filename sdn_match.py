#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sdn_match.py  --  Compare California Principals_Alpha against OFAC SDN entries

Match types
-----------
  Direct  -- case-insensitive exact match
  Soundex -- match on SQL Server-compatible SOUNDEX() code

Matching fields (applied for both match types)
----------------------------------------------
  Last_Name              vs  sdnEntry.lastName   (when present)
  First_Name             vs  sdnEntry.firstName  (when present)
  Org_Name               vs  sdnEntry.lastName   (when present)
  First_Name+Middle_Name vs  sdnEntry.firstName  (when Middle_Name present)
  Middle_Name+Last_Name  vs  sdnEntry.lastName   (when Middle_Name present)

Output columns
--------------
  First_Name | Middle_Name | Last_Name | Org_Name |
  sdnEntry_uid | matchtype | Match_Result |
  editdistance | editdistancesimilarity |
  jaro_winkler_distance | jaro_winkler_similarity

  The four score columns reflect the fuzzy comparison between the specific
  principal field value and the matched SDN entry field value for that row.
  No-match rows leave sdnEntry_uid, matchtype, and all score columns blank.

Usage
-----
    python sdn_match.py [options]

Options
-------
  --ca-server      California SQL Server instance       (default: .)
  --ca-database    California database name             (default: California)
  --sdn-server     SDN SQL Server instance              (default: .)
  --sdn-database   SDN database name                   (default: SDN)
  --ca-connection  Full ODBC string for California DB  (overrides server/db)
  --sdn-connection Full ODBC string for SDN DB         (overrides server/db)
  --output         Output CSV file path                 (default: MatchingResults.csv)
  --entity-name    Filter Principals_Alpha by entity_name prefix.
                   "*" loads all records (default).
                   Any other value filters WHERE entity_name LIKE 'value%'
"""

import argparse
import csv
import sys
from collections import defaultdict

try:
    import pyodbc
except ImportError:
    sys.exit("pyodbc not installed.  Run: pip install pyodbc")

try:
    from doublemetaphone import doublemetaphone as _dm
    _DM_AVAILABLE = True
except ImportError:
    _DM_AVAILABLE = False
    print("WARNING: doublemetaphone package not found — Double-Metaphone matching disabled.\n"
          "         Install with: python -m pip install doublemetaphone")


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------

def _conn_str(server: str, database: str) -> str:
    return (
        f"DRIVER={{ODBC Driver 17 for SQL Server}};"
        f"SERVER={server};DATABASE={database};Trusted_Connection=yes;"
    )


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config(path: str) -> dict:
    """Read match-type scores from the config file. Returns dict matchtype -> int score."""
    import configparser
    cfg = configparser.ConfigParser()
    if not cfg.read(path):
        sys.exit(f"Config file not found: {path}")
    if "MatchTypeScores" not in cfg:
        sys.exit(f"Config file missing [MatchTypeScores] section: {path}")
    scores = {}
    for key, val in cfg["MatchTypeScores"].items():
        # Normalise key to title-case to match matchtype values
        label = key.strip().title().replace("_", " ")
        # Map config keys to exact matchtype strings used in the script
        label_map = {
            "Direct":           "Direct",
            "Soundex":          "Soundex",
            "Jaro-Winkler":     "Jaro-Winkler",
            "Edit Distance":    "Edit Distance",
            "Double-Metaphone": "Double-Metaphone",
            "Nysiis":           "NYSIIS",
        }
        # Find the matching label regardless of capitalisation
        matched = next((v for k, v in label_map.items()
                        if k.lower() == key.strip().lower()), key.strip())
        try:
            scores[matched] = int(val)
        except ValueError:
            sys.exit(f"Non-integer score for '{key}' in {path}: {val!r}")
    print(f"Loaded match-type scores: {scores}")
    return scores


# ---------------------------------------------------------------------------
# Soundex  (matches SQL Server SOUNDEX() behaviour)
# ---------------------------------------------------------------------------

_SDX_CODES = {
    'B': '1', 'F': '1', 'P': '1', 'V': '1',
    'C': '2', 'G': '2', 'J': '2', 'K': '2',
    'Q': '2', 'S': '2', 'X': '2', 'Z': '2',
    'D': '3', 'T': '3',
    'L': '4',
    'M': '5', 'N': '5',
    'R': '6',
}


def _soundex(s: str) -> str | None:
    """4-character Soundex matching SQL Server's SOUNDEX()."""
    if not s:
        return None
    s = ''.join(c for c in s.upper() if c.isalpha())
    if not s:
        return None
    first     = s[0]
    result    = [first]
    prev_code = _SDX_CODES.get(first, '0')
    for ch in s[1:]:
        if ch in 'HW':
            continue
        code = _SDX_CODES.get(ch, '0')
        if code == '0':
            prev_code = '0'
            continue
        if code != prev_code:
            result.append(code)
            if len(result) == 4:
                break
        prev_code = code
    return (''.join(result) + '000')[:4]


# ---------------------------------------------------------------------------
# Double Metaphone
# ---------------------------------------------------------------------------

def _dm_codes(s: str) -> set:
    """Return set of non-empty Double Metaphone codes for s (primary + alternate)."""
    if not s or not _DM_AVAILABLE:
        return set()
    codes = set()
    for word in s.split():
        primary, alternate = _dm(word)
        if primary:
            codes.add(primary)
        if alternate and alternate != primary:
            codes.add(alternate)
    return codes


# ---------------------------------------------------------------------------
# NYSIIS  (implemented without external package)
# ---------------------------------------------------------------------------

_NY_INIT = [
    ('MAC', 'MCC'), ('KN', 'N'), ('K', 'C'),
    ('PH', 'FF'), ('PF', 'FF'), ('SCH', 'SSS'),
]
_NY_TAIL = [
    ('EE', 'Y'), ('IE', 'Y'),
    ('DT', 'D'), ('RT', 'D'), ('RD', 'D'), ('NT', 'D'), ('ND', 'D'),
]


def _nysiis(s: str) -> str | None:
    """NYSIIS phonetic code for a single word."""
    if not s:
        return None
    s = ''.join(c for c in s.upper() if c.isalpha())
    if not s:
        return None
    for old, new in _NY_INIT:
        if s.startswith(old):
            s = new + s[len(old):]
            break
    for old, new in _NY_TAIL:
        if len(s) > len(old) and s.endswith(old):
            s = s[:-len(old)] + new
            break
    key = s[0]
    i = 1
    while i < len(s):
        ch  = s[i]
        prev = key[-1]
        nxt  = s[i + 1] if i + 1 < len(s) else ''
        if s[i:i+3] == 'SCH':
            key += 'SSS'; i += 3; continue
        if s[i:i+2] == 'EV':
            key += 'AF';  i += 2; continue
        if s[i:i+2] == 'PH':
            key += 'FF';  i += 2; continue
        if s[i:i+2] == 'KN':
            key += 'N';   i += 2; continue
        if ch in 'AEIOU':
            key += 'A'; i += 1; continue
        if ch == 'Q': key += 'G'; i += 1; continue
        if ch == 'Z': key += 'S'; i += 1; continue
        if ch == 'M': key += 'N'; i += 1; continue
        if ch == 'K': key += 'C'; i += 1; continue
        if ch == 'H':
            key += prev if (prev not in 'AEIOU' or nxt not in 'AEIOU') else ch
            i += 1; continue
        if ch == 'W':
            key += prev if prev in 'AEIOU' else ch
            i += 1; continue
        key += ch
        i += 1
    result = key[0]
    for c in key[1:]:
        if c != result[-1]:
            result += c
    if len(result) > 1 and result[-1] == 'A':
        result = result[:-1]
    return result


def _nysiis_codes(s: str) -> set:
    """Return set of NYSIIS codes, one per word in s."""
    if not s:
        return set()
    return {code for word in s.split() if (code := _nysiis(word))}


# ---------------------------------------------------------------------------
# Fuzzy scoring algorithms
# ---------------------------------------------------------------------------

def _damerau_levenshtein(s1: str, s2: str) -> int:
    """Damerau-Levenshtein distance (optimal string alignment)."""
    if s1 == s2:
        return 0
    len1, len2 = len(s1), len(s2)
    if len1 == 0:
        return len2
    if len2 == 0:
        return len1
    d = [[0] * (len2 + 1) for _ in range(len1 + 1)]
    for i in range(len1 + 1):
        d[i][0] = i
    for j in range(len2 + 1):
        d[0][j] = j
    for i in range(1, len1 + 1):
        for j in range(1, len2 + 1):
            cost = 0 if s1[i - 1] == s2[j - 1] else 1
            d[i][j] = min(
                d[i - 1][j] + 1,
                d[i][j - 1] + 1,
                d[i - 1][j - 1] + cost,
            )
            if i > 1 and j > 1 and s1[i-1] == s2[j-2] and s1[i-2] == s2[j-1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + cost)
    return d[len1][len2]


def _edit_distance_similarity(s1: str, s2: str, dist: int) -> float:
    """Edit distance similarity as a percentage 0-100."""
    max_len = max(len(s1), len(s2))
    if max_len == 0:
        return 100.0
    return round(100.0 * (1.0 - dist / max_len), 2)


def _jaro(s1: str, s2: str) -> float:
    """Jaro similarity score 0-1."""
    if s1 == s2:
        return 1.0
    len1, len2 = len(s1), len(s2)
    if len1 == 0 or len2 == 0:
        return 0.0
    match_dist = max(len1, len2) // 2 - 1
    if match_dist < 0:
        match_dist = 0
    s1_matched = [False] * len1
    s2_matched = [False] * len2
    matches = 0
    for i in range(len1):
        lo = max(0, i - match_dist)
        hi = min(i + match_dist + 1, len2)
        for j in range(lo, hi):
            if s2_matched[j] or s1[i] != s2[j]:
                continue
            s1_matched[i] = True
            s2_matched[j] = True
            matches += 1
            break
    if matches == 0:
        return 0.0
    transpositions = 0
    k = 0
    for i in range(len1):
        if not s1_matched[i]:
            continue
        while not s2_matched[k]:
            k += 1
        if s1[i] != s2[k]:
            transpositions += 1
        k += 1
    return (matches / len1 + matches / len2 + (matches - transpositions / 2) / matches) / 3


def _jaro_winkler_similarity(s1: str, s2: str, p: float = 0.1) -> float:
    """Jaro-Winkler similarity score 0-1 (higher = more similar)."""
    jaro = _jaro(s1, s2)
    prefix = 0
    for i in range(min(4, len(s1), len(s2))):
        if s1[i] == s2[i]:
            prefix += 1
        else:
            break
    return round(jaro + prefix * p * (1 - jaro), 6)


def _score(principal_val: str, sdn_val: str) -> tuple:
    """
    Return (editdistance, editdistancesimilarity, jw_distance, jw_similarity)
    for two already-lowercased strings.
    """
    ed  = _damerau_levenshtein(principal_val, sdn_val)
    eds = _edit_distance_similarity(principal_val, sdn_val, ed)
    jws = _jaro_winkler_similarity(principal_val, sdn_val)
    jwd = round(1.0 - jws, 6)
    return ed, eds, jwd, jws


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------

def load_sdn(conn_str: str) -> dict:
    """
    Load sdnEntry and build lookup indexes.
    Returns dict with keys:
      by_ln, by_fn         exact lowercase key -> [uid, ...]
      sdx_by_ln, sdx_by_fn soundex key         -> [uid, ...]
      uid_to_ln, uid_to_fn uid                 -> lowercase field value
    """
    print("Loading SDN entries...")
    with pyodbc.connect(conn_str) as conn:
        rows = conn.cursor().execute(
            "SELECT uid, firstName, lastName, sdnType FROM dbo.sdnEntry"
        ).fetchall()

    by_ln          = defaultdict(list)
    by_fn          = defaultdict(list)
    sdx_by_ln      = defaultdict(list)
    sdx_by_fn      = defaultdict(list)
    dm_by_ln       = defaultdict(list)
    dm_by_fn       = defaultdict(list)
    ny_by_ln       = defaultdict(list)
    ny_by_fn       = defaultdict(list)
    uid_to_ln      = {}   # lowercase, for fuzzy scoring
    uid_to_fn      = {}   # lowercase, for fuzzy scoring
    uid_to_ln_orig = {}   # original case, for output columns
    uid_to_fn_orig = {}   # original case, for output columns
    uid_to_sdntype = {}

    for uid, fn, ln, sdn_type in rows:
        if ln:
            key = ln.strip().lower()
            by_ln[key].append(uid)
            uid_to_ln[uid]      = key
            uid_to_ln_orig[uid] = ln.strip()
            sdx = _soundex(key)
            if sdx:
                sdx_by_ln[sdx].append(uid)
            for code in _dm_codes(key):
                dm_by_ln[code].append(uid)
            for code in _nysiis_codes(key):
                ny_by_ln[code].append(uid)
        if fn:
            key = fn.strip().lower()
            by_fn[key].append(uid)
            uid_to_fn[uid]      = key
            uid_to_fn_orig[uid] = fn.strip()
            sdx = _soundex(key)
            if sdx:
                sdx_by_fn[sdx].append(uid)
            for code in _dm_codes(key):
                dm_by_fn[code].append(uid)
            for code in _nysiis_codes(key):
                ny_by_fn[code].append(uid)
        uid_to_sdntype[uid] = sdn_type

    print(f"  {len(rows):,} SDN entries indexed.")
    return {
        "by_ln":          by_ln,          "by_fn":          by_fn,
        "sdx_by_ln":      sdx_by_ln,      "sdx_by_fn":      sdx_by_fn,
        "dm_by_ln":       dm_by_ln,       "dm_by_fn":       dm_by_fn,
        "ny_by_ln":       ny_by_ln,       "ny_by_fn":       ny_by_fn,
        "uid_to_ln":      uid_to_ln,      "uid_to_fn":      uid_to_fn,
        "uid_to_ln_orig": uid_to_ln_orig, "uid_to_fn_orig": uid_to_fn_orig,
        "uid_to_sdntype": uid_to_sdntype,
    }


def load_principals(conn_str: str, entity_name: str = "*") -> list:
    """Return rows from Principals_Alpha, optionally filtered by entity_name prefix."""
    if entity_name == "*":
        sql, params = (
            "SELECT First_Name, Middle_Name, Last_Name, Org_Name "
            "FROM dbo.Principals_Alpha"
        ), []
        print("Loading Principals_Alpha (all records)...")
    else:
        sql, params = (
            "SELECT First_Name, Middle_Name, Last_Name, Org_Name "
            "FROM dbo.Principals_Alpha WHERE entity_name LIKE ?"
        ), [entity_name + "%"]
        print(f"Loading Principals_Alpha WHERE entity_name LIKE '{entity_name}%'...")

    with pyodbc.connect(conn_str) as conn:
        rows = conn.cursor().execute(sql, params).fetchall()
    print(f"  {len(rows):,} principals loaded.")
    return rows


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def _clean(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s.lower() if s else None


def match_principal(first, middle, last, org, idx: dict) -> list:
    """
    Return list of (uid, label, matchtype, principal_val, sdn_field)
      principal_val : the principal's lowercased field value used in comparison
      sdn_field     : 'ln' or 'fn' — which SDN field to look up for scoring
    Returns empty list if nothing matched.
    """
    by_ln     = idx["by_ln"]
    by_fn     = idx["by_fn"]
    sdx_by_ln = idx["sdx_by_ln"]
    sdx_by_fn = idx["sdx_by_fn"]
    dm_by_ln  = idx["dm_by_ln"]
    dm_by_fn  = idx["dm_by_fn"]
    ny_by_ln  = idx["ny_by_ln"]
    ny_by_fn  = idx["ny_by_fn"]

    last_key   = _clean(last)
    first_key  = _clean(first)
    org_key    = _clean(org)
    middle_key = _clean(middle)

    hits = []   # (uid, label, matchtype, principal_val, sdn_field)

    # lastName comparisons
    for key, label in [
        (last_key, "Matched on Last_Name"),
        (org_key,  "Matched on Org_Name"),
    ]:
        if not key:
            continue
        for uid in by_ln.get(key, []):
            hits.append((uid, label, "Direct", key, "ln"))
        sdx = _soundex(key)
        if sdx:
            for uid in sdx_by_ln.get(sdx, []):
                hits.append((uid, label, "Soundex", key, "ln"))
        for code in _dm_codes(key):
            for uid in dm_by_ln.get(code, []):
                hits.append((uid, label, "Double-Metaphone", key, "ln"))
        for code in _nysiis_codes(key):
            for uid in ny_by_ln.get(code, []):
                hits.append((uid, label, "NYSIIS", key, "ln"))

    if middle_key and last_key:
        key, label = f"{middle_key} {last_key}", "Matched on Middle_Name + Last_Name"
        for uid in by_ln.get(key, []):
            hits.append((uid, label, "Direct", key, "ln"))
        sdx = _soundex(key)
        if sdx:
            for uid in sdx_by_ln.get(sdx, []):
                hits.append((uid, label, "Soundex", key, "ln"))
        for code in _dm_codes(key):
            for uid in dm_by_ln.get(code, []):
                hits.append((uid, label, "Double-Metaphone", key, "ln"))
        for code in _nysiis_codes(key):
            for uid in ny_by_ln.get(code, []):
                hits.append((uid, label, "NYSIIS", key, "ln"))

    # firstName comparisons
    if first_key:
        label = "Matched on First_Name"
        for uid in by_fn.get(first_key, []):
            hits.append((uid, label, "Direct", first_key, "fn"))
        sdx = _soundex(first_key)
        if sdx:
            for uid in sdx_by_fn.get(sdx, []):
                hits.append((uid, label, "Soundex", first_key, "fn"))
        for code in _dm_codes(first_key):
            for uid in dm_by_fn.get(code, []):
                hits.append((uid, label, "Double-Metaphone", first_key, "fn"))
        for code in _nysiis_codes(first_key):
            for uid in ny_by_fn.get(code, []):
                hits.append((uid, label, "NYSIIS", first_key, "fn"))

    if middle_key and first_key:
        key, label = f"{first_key} {middle_key}", "Matched on First_Name + Middle_Name"
        for uid in by_fn.get(key, []):
            hits.append((uid, label, "Direct", key, "fn"))
        sdx = _soundex(key)
        if sdx:
            for uid in sdx_by_fn.get(sdx, []):
                hits.append((uid, label, "Soundex", key, "fn"))
        for code in _dm_codes(key):
            for uid in dm_by_fn.get(code, []):
                hits.append((uid, label, "Double-Metaphone", key, "fn"))
        for code in _nysiis_codes(key):
            for uid in ny_by_fn.get(code, []):
                hits.append((uid, label, "NYSIIS", key, "fn"))

    return hits


# ---------------------------------------------------------------------------
# Output table DDL
# ---------------------------------------------------------------------------

_DDL_DETAIL = """
CREATE TABLE [{s}].[MatchingResults] (
    [ID]                       INT            NOT NULL IDENTITY PRIMARY KEY,
    [run_id]                   INT            NOT NULL,
    [First_Name]               NVARCHAR(255)  NULL,
    [Middle_Name]              NVARCHAR(255)  NULL,
    [Last_Name]                NVARCHAR(500)  NULL,
    [Org_Name]                 NVARCHAR(900)  NULL,
    [sdnEntry_uid]             INT            NULL,
    [sdnFirstName]             NVARCHAR(255)  NULL,
    [sdnLastName]              NVARCHAR(500)  NULL,
    [sdnType]                  NVARCHAR(255)  NULL,
    [matchtype]                NVARCHAR(50)   NULL,
    [match_score]              INT            NULL,
    [Match_Result]             NVARCHAR(255)  NULL,
    [editdistance]             INT            NULL,
    [editdistancesimilarity]   DECIMAL(6,2)   NULL,
    [jaro_winkler_distance]    DECIMAL(8,6)   NULL,
    [jaro_winkler_similarity]  DECIMAL(8,6)   NULL
);
"""

_DDL_SUMMARY = """
CREATE TABLE [{s}].[MatchingResults_Summary] (
    [ID]                           INT            NOT NULL IDENTITY PRIMARY KEY,
    [run_id]                       INT            NOT NULL,
    [First_Name]                   NVARCHAR(255)  NULL,
    [Middle_Name]                  NVARCHAR(255)  NULL,
    [Last_Name]                    NVARCHAR(500)  NULL,
    [Org_Name]                     NVARCHAR(900)  NULL,
    [total_matches]                INT            NOT NULL DEFAULT 0,
    [direct_matches]               INT            NOT NULL DEFAULT 0,
    [soundex_matches]              INT            NOT NULL DEFAULT 0,
    [double_metaphone_matches]     INT            NOT NULL DEFAULT 0,
    [nysiis_matches]               INT            NOT NULL DEFAULT 0,
    [best_uid]                     INT            NULL,
    [best_match_field]             NVARCHAR(255)  NULL,
    [best_matchtype]               NVARCHAR(50)   NULL,
    [composite_score]              INT            NULL,
    [best_editdistance]            INT            NULL,
    [best_editdistancesimilarity]  DECIMAL(6,2)   NULL,
    [best_jaro_winkler_distance]   DECIMAL(8,6)   NULL,
    [best_jaro_winkler_similarity] DECIMAL(8,6)   NULL,
    [all_matched_uids]             NVARCHAR(MAX)  NULL
);
"""

_DDL_RUN_LOG = """
IF OBJECT_ID(N'[{s}].[MatchingResults_RunLog]', N'U') IS NULL
CREATE TABLE [{s}].[MatchingResults_RunLog] (
    [run_id]          INT            NOT NULL IDENTITY PRIMARY KEY,
    [run_date]        DATETIME       NOT NULL DEFAULT GETDATE(),
    [entity_name_filter] NVARCHAR(255) NULL,
    [principals_checked] INT         NOT NULL DEFAULT 0,
    [direct_matches]  INT            NOT NULL DEFAULT 0,
    [soundex_matches] INT            NOT NULL DEFAULT 0,
    [no_matches]      INT            NOT NULL DEFAULT 0
);
"""


def setup_output_tables(conn, schema: str, drop: bool):
    """Create output tables. RunLog is never dropped. Detail/Summary are dropped
    if --drop-output is set, then created if they don't already exist."""
    cursor = conn.cursor()
    # RunLog accumulates history — never drop it
    cursor.execute(_DDL_RUN_LOG.replace("{s}", schema))
    # Add new phonetic match columns if upgrading from an earlier run
    for col in ('double_metaphone_matches', 'nysiis_matches'):
        cursor.execute(
            f"IF NOT EXISTS ("
            f"  SELECT 1 FROM sys.columns "
            f"  WHERE object_id = OBJECT_ID(N'[{schema}].[MatchingResults_RunLog]') "
            f"  AND name = '{col}'"
            f") ALTER TABLE [{schema}].[MatchingResults_RunLog] "
            f"ADD [{col}] INT NOT NULL DEFAULT 0"
        )
    if drop:
        for tname in ("MatchingResults_Summary", "MatchingResults"):
            cursor.execute(
                f"IF OBJECT_ID(N'[{schema}].[{tname}]', N'U') IS NOT NULL "
                f"DROP TABLE [{schema}].[{tname}];"
            )
    # Create detail and summary tables if they don't exist yet
    for ddl in (_DDL_DETAIL, _DDL_SUMMARY):
        stmt = ddl.replace("{s}", schema)
        tname = "MatchingResults" if "MatchingResults_Summary" not in stmt else "MatchingResults_Summary"
        cursor.execute(
            f"IF OBJECT_ID(N'[{schema}].[{tname}]', N'U') IS NULL {stmt}"
        )
    conn.commit()


def create_run(conn, schema: str, entity_filter: str) -> int:
    """Insert a row into RunLog and return the new run_id."""
    cursor = conn.cursor()
    cursor.execute(
        f"INSERT INTO [{schema}].[MatchingResults_RunLog] (entity_name_filter) "
        f"OUTPUT INSERTED.run_id VALUES (?)",
        [entity_filter]
    )
    run_id = int(cursor.fetchone()[0])
    conn.commit()
    return run_id


def update_run(conn, schema: str, run_id: int,
               principals: int, direct: int, soundex: int,
               double_metaphone: int, nysiis: int, no_match: int):
    """Update the RunLog row with final counts."""
    conn.cursor().execute(
        f"UPDATE [{schema}].[MatchingResults_RunLog] "
        f"SET principals_checked=?, direct_matches=?, soundex_matches=?, "
        f"double_metaphone_matches=?, nysiis_matches=?, no_matches=? "
        f"WHERE run_id=?",
        [principals, direct, soundex, double_metaphone, nysiis, no_match, run_id]
    )
    conn.commit()


def insert_detail(conn, schema: str, run_id: int,
                  rows: list, uid_to_fn_orig: dict, uid_to_ln_orig: dict,
                  uid_to_sdntype: dict, batch_size: int = 500):
    sql = (
        f"INSERT INTO [{schema}].[MatchingResults] "
        f"(run_id, First_Name, Middle_Name, Last_Name, Org_Name, "
        f"sdnEntry_uid, sdnFirstName, sdnLastName, sdnType, "
        f"matchtype, match_score, Match_Result, "
        f"editdistance, editdistancesimilarity, "
        f"jaro_winkler_distance, jaro_winkler_similarity) "
        f"VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
    )
    cursor = conn.cursor()

    def _none(v):
        return None if v == "" else v

    tagged = [
        (run_id, r[0], r[1], r[2], r[3],
         r[4],
         uid_to_fn_orig.get(r[4]),
         uid_to_ln_orig.get(r[4]),
         uid_to_sdntype.get(r[4]),
         r[5], r[11], r[6],
         _none(r[7]), _none(r[8]), _none(r[9]), _none(r[10]))
        for r in rows
    ]
    for i in range(0, len(tagged), batch_size):
        cursor.executemany(sql, tagged[i:i + batch_size])
    conn.commit()


def insert_summary(conn, schema: str, run_id: int,
                   rows: list, batch_size: int = 500):
    sql = (
        f"INSERT INTO [{schema}].[MatchingResults_Summary] "
        f"(run_id, First_Name, Middle_Name, Last_Name, Org_Name, "
        f"total_matches, direct_matches, soundex_matches, "
        f"double_metaphone_matches, nysiis_matches, "
        f"best_uid, best_match_field, best_matchtype, composite_score, "
        f"best_editdistance, best_editdistancesimilarity, "
        f"best_jaro_winkler_distance, best_jaro_winkler_similarity, "
        f"all_matched_uids) "
        f"VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
    )
    cursor = conn.cursor()

    def _none(v):
        return None if v == "" else v

    # Summary tuple layout (indices):
    #  0-3: first, middle, last, org
    #  4-8: total, direct, soundex, double_metaphone, nysiis
    #  9-11: best_uid, best_field, best_matchtype
    #  12-15: best_ed, best_eds, best_jwd, best_jws
    #  16: all_uids   17: composite_score
    tagged = [
        (run_id, r[0], r[1], r[2], r[3],
         r[4], r[5], r[6], r[7], r[8],
         r[9], r[10], r[11], r[17],
         _none(r[12]), _none(r[13]), _none(r[14]), _none(r[15]),
         r[16] or None)
        for r in rows
    ]
    for i in range(0, len(tagged), batch_size):
        cursor.executemany(sql, tagged[i:i + batch_size])
    conn.commit()


# ---------------------------------------------------------------------------
# Summary builder
# ---------------------------------------------------------------------------

_MATCHTYPE_RANK = {"Direct": 0, "Double-Metaphone": 1, "NYSIIS": 1, "Soundex": 2}


def build_summary(detail_rows: list) -> list:
    """
    Collapse detail rows into one summary row per principal.

    Summary columns:
      First_Name, Middle_Name, Last_Name, Org_Name,
      total_matches, direct_matches, soundex_matches,
      best_uid, best_match_field, best_matchtype,
      best_editdistance, best_editdistancesimilarity,
      best_jaro_winkler_distance, best_jaro_winkler_similarity,
      all_matched_uids   (pipe-delimited, deduplicated, in match order)

    Best match is chosen by: Direct before Soundex, then highest
    jaro_winkler_similarity, then lowest editdistance.
    """
    from collections import OrderedDict

    # Group rows by principal identity
    groups: dict = OrderedDict()
    for row in detail_rows:
        first, middle, last, org = row[0], row[1], row[2], row[3]
        key = (first, middle, last, org)
        if key not in groups:
            groups[key] = []
        groups[key].append(row)

    summary = []
    for (first, middle, last, org), rows in groups.items():
        # Separate match rows from no-match rows
        match_rows = [r for r in rows if r[6] != "No Match"]

        if not match_rows:
            summary.append((
                first, middle, last, org,
                0, 0, 0, 0, 0,
                None, "No Match", None,
                "", "", "", "",
                "",
                0,
            ))
            continue

        direct_count  = sum(1 for r in match_rows if r[5] == "Direct")
        soundex_count = sum(1 for r in match_rows if r[5] == "Soundex")
        dm_count      = sum(1 for r in match_rows if r[5] == "Double-Metaphone")
        ny_count      = sum(1 for r in match_rows if r[5] == "NYSIIS")

        # Sort to find best: Direct first, then highest JWS, then lowest ED
        def rank(r):
            matchtype_pri = _MATCHTYPE_RANK.get(r[5], 99)
            jws = r[10] if isinstance(r[10], float) else -1.0
            ed  = r[7]  if isinstance(r[7],  int)   else 99999
            return (matchtype_pri, -jws, ed)

        best = sorted(match_rows, key=rank)[0]

        # Deduplicated uid list in appearance order
        seen_uids = []
        seen_set  = set()
        for r in match_rows:
            uid = r[4]
            if uid is not None and uid not in seen_set:
                seen_uids.append(str(uid))
                seen_set.add(uid)

        composite = sum(r[11] for r in match_rows if isinstance(r[11], int))

        summary.append((
            first, middle, last, org,              # 0-3
            len(match_rows), direct_count,          # 4-5
            soundex_count, dm_count, ny_count,      # 6-8
            best[4],   # best_uid                  9
            best[6],   # best_match_field           10
            best[5],   # best_matchtype             11
            best[7],   # best_editdistance          12
            best[8],   # best_editdistancesimilarity 13
            best[9],   # best_jaro_winkler_distance 14
            best[10],  # best_jaro_winkler_similarity 15
            " | ".join(seen_uids),                  # 16
            composite,                              # 17
        ))

    return summary


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Compare Principals_Alpha against OFAC SDN entries",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--ca-server",       default=".",          help="California DB server")
    ap.add_argument("--ca-database",     default="California", help="California database")
    ap.add_argument("--sdn-server",      default=".",          help="SDN DB server")
    ap.add_argument("--sdn-database",    default="SDN",        help="SDN database")
    ap.add_argument("--ca-connection",   default="",           help="Full ODBC string for California DB")
    ap.add_argument("--sdn-connection",  default="",           help="Full ODBC string for SDN DB")
    ap.add_argument("--out-server",      default="",           help="Output DB server (default: same as --ca-server)")
    ap.add_argument("--out-database",    default="SDNReporting", help="Output database   (default: SDNReporting)")
    ap.add_argument("--out-schema",      default="dbo",        help="Output schema     (default: dbo)")
    ap.add_argument("--out-connection",  default="",           help="Full ODBC string for output DB")
    ap.add_argument("--drop-output",     action="store_true",  help="DROP and recreate MatchingResults tables before inserting")
    ap.add_argument("--no-csv",          action="store_true",  help="Skip CSV file output")
    ap.add_argument("--output",          default="MatchingResults.csv", help="Output CSV path")
    ap.add_argument("--entity-name",     default="*",
                    help="Filter by entity_name prefix; '*' = all (default)")
    ap.add_argument("--config",          default="sdn_match.cfg",
                    help="Path to config file (default: sdn_match.cfg)")
    args = ap.parse_args()

    scores = load_config(args.config)

    ca_cs  = args.ca_connection  or _conn_str(args.ca_server,  args.ca_database)
    sdn_cs = args.sdn_connection or _conn_str(args.sdn_server, args.sdn_database)
    out_cs = (args.out_connection
              or _conn_str(args.out_server or args.ca_server, args.out_database))

    idx        = load_sdn(sdn_cs)
    principals = load_principals(ca_cs, entity_name=args.entity_name)

    uid_to_ln = idx["uid_to_ln"]
    uid_to_fn = idx["uid_to_fn"]

    print("Comparing...")
    output_rows    = []
    no_match_count = 0
    type_counts    = defaultdict(int)

    for first, middle, last, org in principals:
        hits = match_principal(first, middle, last, org, idx)
        if hits:
            for uid, label, matchtype, principal_val, sdn_field in hits:
                sdn_val = uid_to_ln.get(uid) if sdn_field == "ln" else uid_to_fn.get(uid)
                if sdn_val:
                    ed, eds, jwd, jws = _score(principal_val, sdn_val)
                else:
                    ed, eds, jwd, jws = "", "", "", ""
                output_rows.append((
                    first, middle, last, org,
                    uid, matchtype, label,
                    ed, eds, jwd, jws,
                    scores.get(matchtype, 0),
                ))
                type_counts[matchtype] += 1
        else:
            output_rows.append((
                first, middle, last, org,
                None, None, "No Match",
                "", "", "", "",
                0,
            ))
            no_match_count += 1

    summary_rows = build_summary(output_rows)
    s = args.out_schema

    # ----------------------------------------------------------------
    # Database output
    # ----------------------------------------------------------------
    print(f"Writing to database [{args.out_database or args.ca_database}].[{s}]...")
    with pyodbc.connect(out_cs) as conn:
        setup_output_tables(conn, s, drop=args.drop_output)
        run_id = create_run(conn, s, args.entity_name)
        print(f"  run_id = {run_id}")

        print(f"  Inserting {len(output_rows):,} detail rows...")
        insert_detail(conn, s, run_id, output_rows,
                      idx["uid_to_fn_orig"], idx["uid_to_ln_orig"], idx["uid_to_sdntype"])

        print(f"  Inserting {len(summary_rows):,} summary rows...")
        insert_summary(conn, s, run_id, summary_rows)

        update_run(conn, s, run_id,
                   len(principals),
                   type_counts["Direct"],
                   type_counts["Soundex"],
                   type_counts["Double-Metaphone"],
                   type_counts["NYSIIS"],
                   no_match_count)

    # ----------------------------------------------------------------
    # Optional CSV output
    # ----------------------------------------------------------------
    if not args.no_csv:
        base, ext = args.output.rsplit(".", 1) if "." in args.output else (args.output, "csv")
        summary_path = f"{base}_Summary.{ext}"

        print(f"Writing {args.output} ...")
        with open(args.output, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "First_Name", "Middle_Name", "Last_Name", "Org_Name",
                "sdnEntry_uid", "matchtype", "Match_Result",
                "editdistance", "editdistancesimilarity",
                "jaro_winkler_distance", "jaro_winkler_similarity",
                "match_score",
            ])
            writer.writerows(output_rows)

        print(f"Writing {summary_path} ...")
        with open(summary_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "First_Name", "Middle_Name", "Last_Name", "Org_Name",
                "total_matches", "direct_matches", "soundex_matches",
                "double_metaphone_matches", "nysiis_matches",
                "best_uid", "best_match_field", "best_matchtype",
                "best_editdistance", "best_editdistancesimilarity",
                "best_jaro_winkler_distance", "best_jaro_winkler_similarity",
                "all_matched_uids", "composite_score",
            ])
            writer.writerows(summary_rows)

    print(
        f"\nDone.  run_id = {run_id}\n"
        f"  {len(principals):,}  principals checked\n"
        f"  {type_counts['Direct']:,}  Direct match rows\n"
        f"  {type_counts['Double-Metaphone']:,}  Double-Metaphone match rows\n"
        f"  {type_counts['NYSIIS']:,}  NYSIIS match rows\n"
        f"  {type_counts['Soundex']:,}  Soundex match rows\n"
        f"  {no_match_count:,}  principals with no match"
    )


if __name__ == "__main__":
    main()
