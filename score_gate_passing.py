#!/usr/bin/env python3
"""
score_gate_passing.py -- 100-point composite scoring for MatchingResults_GatePassing.

Score breakdown (configurable via ScoringWeights table in SDNReporting DB):
  NameSimilarity  0 – 60   Individual: avg(FN_JW, LN_JW) * 60
                            Org: TokenMatch (0-20) + FuzzyMatch (0-40)
  NameContext     0 –  5   Primary=5, StrongAKA=4, WeakAKA=2
  EntityType      0 – 10   10 if Individual↔Individual or Entity↔Entity, else 0
  Address         0 – 15   Full=15; CityCountry/RegionCountry/Country interpolated
  Country         0 – 10   10 if Country_JW = 100, else 0
  ──────────────────────
  Total           0 – 100

Weights are read from dbo.ScoringWeights in the output database; missing keys
fall back to the defaults compiled into _WEIGHT_DEFAULTS.

Usage
─────
  python score_gate_passing.py --run-id 42 \\
      --out-server <fqdn> --out-database SDNReporting \\
      --sdn-server <fqdn> --sdn-database SDN

Environment variables (override CLI defaults):
  SQL_SERVER    default server for both --out-server and --sdn-server
  SQL_USER      SQL login (omit for Windows auth / Trusted_Connection)
  SQL_PASSWORD  SQL password
"""

import argparse
import os
import re
import sys

import pyodbc

# Reuse the JW engine and stop-word set from the matching pipeline.
# Both are module-level constants/functions with no side-effects on import.
try:
    from sdn_match_v2 import _jaro_winkler_fast, _ORG_STOP_WORDS
except ImportError:
    sys.exit(
        "ERROR: sdn_match_v2.py not found.  "
        "Run score_gate_passing.py from the same directory as sdn_match_v2.py."
    )


# ---------------------------------------------------------------------------
# Connection helper
# ---------------------------------------------------------------------------

def _conn_str(server: str, database: str) -> str:
    user = os.environ.get('SQL_USER')
    pwd  = os.environ.get('SQL_PASSWORD')
    drv  = os.environ.get('SQL_DRIVER', 'ODBC Driver 17 for SQL Server')
    if user and pwd:
        return (f"DRIVER={{{drv}}};SERVER={server};DATABASE={database};"
                f"UID={user};PWD={pwd};Encrypt=yes;TrustServerCertificate=no;"
                "Connection Timeout=30;")
    return (f"DRIVER={{{drv}}};SERVER={server};DATABASE={database};"
            "Trusted_Connection=yes;")


# ---------------------------------------------------------------------------
# Scoring weight defaults
# ---------------------------------------------------------------------------

_WEIGHT_DEFAULTS: dict = {
    # Name Similarity (0-45): orgs = 30 word match + 15 fuzzy; individuals = 45 JW avg
    'NameSimilarity_Max':        45.0,
    'TokenMatch_All':            30.0,
    'TokenMatch_FirstLast_Min':  24.0,
    'TokenMatch_FirstLast_Max':  27.0,
    'TokenMatch_Majority_Min':   15.0,
    'TokenMatch_Majority_Max':   22.5,
    'TokenMatch_Minority_Min':    7.5,
    'TokenMatch_Minority_Max':   13.5,
    'FuzzyMatch_Max':            15.0,
    # Name Context (0-15)
    'NameContext_Primary':       15.0,
    'NameContext_StrongAKA':     12.0,
    'NameContext_WeakAKA':        6.0,
    # Entity Type (0 or 15)
    'EntityType_Match':          15.0,
    # Address (0-15)
    'Address_Full':              15.0,
    'Address_CityCountry_Min':   10.0,
    'Address_CityCountry_Max':   12.0,
    'Address_RegionCountry_Min':  6.0,
    'Address_RegionCountry_Max':  9.0,
    'Address_Country_Min':        3.0,
    'Address_Country_Max':        5.0,
    'Address_JW_Threshold':      85.0,
    # Country/Jurisdiction (0-10)
    'Country_Full':              10.0,
    # Internal
    'WordMatch_JW_Threshold':    75.0,
}


def load_weights(conn, schema: str) -> dict:
    """Load ScoringWeights from DB, filling any missing keys from defaults."""
    w = dict(_WEIGHT_DEFAULTS)
    try:
        rows = conn.cursor().execute(
            f"SELECT Weight_Key, Weight_Value FROM [{schema}].[ScoringWeights]"
        ).fetchall()
        for key, val in rows:
            if key in w:
                w[key] = float(val)
    except Exception as exc:
        print(f"  WARNING: could not read ScoringWeights ({exc}); using built-in defaults.")
    return w


# ---------------------------------------------------------------------------
# SDN name lookup (for org token analysis)
# ---------------------------------------------------------------------------

def _norm(s: str) -> str:
    """Lowercase + collapse whitespace."""
    return re.sub(r'\s+', ' ', (s or '').strip().lower())


def load_sdn_names(sdn_conn) -> tuple[dict, dict]:
    """
    Returns
    -------
    org_names : {sdn_uid: norm_name}
        Entity-type sdnEntry names (used to look up SDN org name for token analysis).
    aka_info  : {(sdn_uid, aka_uid): (norm_name, category)}
        All AKA entries.  norm_name is empty for Individual AKAs (only category
        is needed for NameContext scoring of those rows).
    """
    org_rows = sdn_conn.cursor().execute(
        "SELECT uid, lastName FROM dbo.sdnEntry WHERE sdnType = 'entity'"
    ).fetchall()
    org_names = {uid: _norm(ln) for uid, ln in org_rows if ln}

    aka_rows = sdn_conn.cursor().execute("""
        SELECT e.uid, a.uid, a.lastName, a.category, e.sdnType
        FROM   dbo.akaList a
        JOIN   dbo.sdnEntry_akaList ea ON ea.akaList_uid = a.uid
        JOIN   dbo.sdnEntry e          ON e.uid          = ea.sdnEntry_uid
    """).fetchall()

    aka_info: dict = {}
    for sdn_uid, aka_uid, ln, cat, sdt in aka_rows:
        nm = _norm(ln) if (sdt == 'entity' and ln) else ''
        aka_info[(sdn_uid, aka_uid)] = (nm, (cat or '').lower())

    return org_names, aka_info


# ---------------------------------------------------------------------------
# Token-match analysis (org names only)
# ---------------------------------------------------------------------------

def _tokenize(name: str) -> list[str]:
    """Lowercase, split on whitespace, remove stop words."""
    return [w for w in name.lower().split() if w not in _ORG_STOP_WORDS]


def _token_match_category(input_nm: str, sdn_nm: str,
                           jw_threshold: float) -> tuple[str, float]:
    """
    Classify the word-level match between two (already normalised) org names.

    Returns
    -------
    category : 'All' | 'FirstLast' | 'Majority' | 'Minority' | 'None'
    fraction : 0-1 value for interpolation within the category band
    """
    inp = _tokenize(input_nm or '')
    sdn = _tokenize(sdn_nm  or '')
    if not inp or not sdn:
        return 'None', 0.0

    thresh = jw_threshold / 100.0

    def _matches_any(word: str, pool: list[str]) -> bool:
        return any(_jaro_winkler_fast(word, p) >= thresh for p in pool)

    matched_inp = [w for w in inp if _matches_any(w, sdn)]
    matched_sdn = [s for s in sdn if _matches_any(s, inp)]

    n_inp, n_sdn = len(inp), len(sdn)
    n_mi, n_ms   = len(matched_inp), len(matched_sdn)
    ratio = n_mi / max(n_inp, n_sdn)

    # All words match (both directions)
    if n_mi == n_inp and n_ms == n_sdn:
        return 'All', 1.0

    # First + last input word match something in SDN,
    # AND majority of middle SDN words also match.
    if (n_inp >= 2 and n_sdn >= 2
            and _matches_any(inp[0],  sdn)
            and _matches_any(inp[-1], sdn)):
        middle_sdn = sdn[1:-1]
        if middle_sdn:
            mid_matched = sum(1 for s in middle_sdn if _matches_any(s, inp))
            mid_ratio   = mid_matched / len(middle_sdn)
        else:
            mid_ratio = 1.0
        if mid_ratio >= 0.5:
            return 'FirstLast', mid_ratio   # fraction ∈ [0.5, 1.0]

    if ratio > 0.5:
        return 'Majority', ratio
    if ratio > 0.0:
        return 'Minority', ratio
    return 'None', 0.0


# ---------------------------------------------------------------------------
# Linear interpolation helper
# ---------------------------------------------------------------------------

def _interp(lo: float, hi: float, jw: float, floor: float) -> float:
    """Map jw in [floor, 100] linearly onto [lo, hi]."""
    span = 100.0 - floor
    if span <= 0:
        return lo
    return lo + (hi - lo) * min(max(jw - floor, 0.0), span) / span


# ---------------------------------------------------------------------------
# Score one GatePassing row
# ---------------------------------------------------------------------------

def score_row(row, org_names: dict, aka_info: dict, w: dict) -> dict:
    """
    Parameters
    ----------
    row       : pyodbc Row with columns from the SELECT in main()
    org_names : {sdn_uid: norm_name}
    aka_info  : {(sdn_uid, aka_uid): (norm_name, category)}
    w         : weights dict from load_weights()

    Returns
    -------
    dict with keys: NameSimilarity, NameContext, EntityType,
                    Address, Country, TokenMatchCategory, Total
    """
    input_type = (row.Input_Type or '').lower()    # 'individual' | 'entity'
    sdn_type   = (row.SDN_Type   or '').lower()    # 'individual' | 'entity' | 'vessel' | ...
    name_type  = (row.SDN_Name_Type or '').lower() # 'regular' | 'aka'
    aka_uid    = row.SDN_AKA_UID
    sdn_uid    = row.SDN_UID

    # ---- Name Similarity (0 – 60) ----------------------------------------
    tok_cat = None

    if input_type == 'individual':
        fn_jw    = float(row.FN_JW or 0)
        ln_jw    = float(row.LN_JW or 0)
        name_sim = (fn_jw + ln_jw) / 2.0 / 100.0 * w['NameSimilarity_Max']

    else:  # entity / org
        org_jw     = float(row.OrgNM_JW or 0)
        fuzzy_pts  = org_jw / 100.0 * w['FuzzyMatch_Max']

        # Resolve SDN name for positional token analysis
        if aka_uid is not None:
            info   = aka_info.get((sdn_uid, aka_uid))
            sdn_nm = info[0] if info else ''
        else:
            sdn_nm = org_names.get(sdn_uid, '')

        jw_wt = w['WordMatch_JW_Threshold']
        tok_cat, frac = _token_match_category(
            row.InputOrgNM or '', sdn_nm, jw_wt)

        if tok_cat == 'All':
            tok_pts = w['TokenMatch_All']
        elif tok_cat == 'FirstLast':
            # frac ∈ [0.5, 1.0] → map onto [0, 100] with floor 50
            tok_pts = _interp(w['TokenMatch_FirstLast_Min'],
                              w['TokenMatch_FirstLast_Max'],
                              frac * 100, 50.0)
        elif tok_cat == 'Majority':
            # frac ∈ (0.5, 1.0] → map onto [0, 100] with floor 50
            tok_pts = _interp(w['TokenMatch_Majority_Min'],
                              w['TokenMatch_Majority_Max'],
                              frac * 100, 50.0)
        elif tok_cat == 'Minority':
            # frac ∈ (0.0, 0.5] → map onto [0, 100] with floor 0
            tok_pts = _interp(w['TokenMatch_Minority_Min'],
                              w['TokenMatch_Minority_Max'],
                              frac * 100, 0.0)
        else:
            tok_pts = 0.0

        name_sim = tok_pts + fuzzy_pts

    # ---- Name Context (0 – 5) --------------------------------------------
    if name_type == 'regular':
        ctx_pts = w['NameContext_Primary']
    else:
        # AKA — resolve category from aka_info (covers both Individual and Entity AKAs)
        cat = ''
        if aka_uid is not None:
            info = aka_info.get((sdn_uid, aka_uid))
            cat  = info[1] if info else ''
        ctx_pts = (w['NameContext_StrongAKA'] if cat == 'strong'
                   else w['NameContext_WeakAKA'])

    # ---- Entity Type (0 or 10) -------------------------------------------
    if ((input_type == 'individual' and sdn_type == 'individual') or
            (input_type == 'entity' and sdn_type == 'entity')):
        etype_pts = w['EntityType_Match']
    else:
        etype_pts = 0.0

    # ---- Address (0 – 15, best tier wins) --------------------------------
    thr      = w['Address_JW_Threshold']
    full_jw  = float(row.FullAddress_JW   or 0)
    cc_jw    = float(row.CityCountry_JW   or 0)
    rc_jw    = float(row.RegionCountry_JW or 0)
    co_jw    = float(row.Country_JW       or 0)

    if full_jw >= thr:
        addr_pts  = w['Address_Full']
        addr_type = 'Full'
    elif cc_jw >= thr:
        addr_pts  = _interp(w['Address_CityCountry_Min'],
                             w['Address_CityCountry_Max'], cc_jw, thr)
        addr_type = 'CityCountry'
    elif rc_jw >= thr:
        addr_pts  = _interp(w['Address_RegionCountry_Min'],
                             w['Address_RegionCountry_Max'], rc_jw, thr)
        addr_type = 'RegionCountry'
    elif co_jw >= thr:
        addr_pts  = _interp(w['Address_Country_Min'],
                             w['Address_Country_Max'], co_jw, thr)
        addr_type = 'Country'
    else:
        addr_pts  = 0.0
        addr_type = 'None'

    # ---- Country (0 or 10) -----------------------------------------------
    country_pts = w['Country_Full'] if co_jw >= 100.0 else 0.0

    total = round(min(100.0,
                      name_sim + ctx_pts + etype_pts + addr_pts + country_pts), 2)

    return {
        'NameSimilarity':     round(name_sim,    2),
        'NameContext':        round(ctx_pts,      2),
        'EntityType':         round(etype_pts,    2),
        'Address':            round(addr_pts,     2),
        'Country':            round(country_pts,  2),
        'TokenMatchCategory': tok_cat,
        'AddressMatchType':   addr_type,
        'Total':              total,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description='Score MatchingResults_GatePassing rows (100-pt composite)'
    )
    _default_srv = os.environ.get('SQL_SERVER', '.')
    run_grp = ap.add_mutually_exclusive_group(required=True)
    run_grp.add_argument('--run-id',   type=int,
                         help='Specific Run_ID to score')
    run_grp.add_argument('--last-run', action='store_true',
                         help='Score the most recent run in MatchingResults_v2_RunLog')
    ap.add_argument('--out-server',   default=_default_srv,
                    help='SDNReporting server FQDN (default: SQL_SERVER env or .)')
    ap.add_argument('--out-database', default='SDNReporting')
    ap.add_argument('--out-schema',   default='dbo')
    ap.add_argument('--sdn-server',   default=_default_srv,
                    help='SDN source server FQDN (default: SQL_SERVER env or .)')
    ap.add_argument('--sdn-database', default='SDN')
    ap.add_argument('--batch-size',   type=int, default=2000,
                    help='UPDATE batch size (default: 2000)')
    args = ap.parse_args()

    schema = args.out_schema

    out_cs = _conn_str(args.out_server, args.out_database)
    sdn_cs = _conn_str(args.sdn_server, args.sdn_database)

    if args.last_run:
        with pyodbc.connect(out_cs) as _c:
            row = _c.cursor().execute(
                f"SELECT TOP 1 run_id FROM [{schema}].[MatchingResults_v2_RunLog]"
                " ORDER BY run_id DESC"
            ).fetchone()
            if not row:
                sys.exit("ERROR: no runs found in MatchingResults_v2_RunLog.")
            run_id = int(row[0])
        print(f"--last-run resolved to Run_ID {run_id}.")
    else:
        run_id = args.run_id

    print(f"Scoring Run_ID {run_id} ...")

    with pyodbc.connect(out_cs) as out_conn, \
         pyodbc.connect(sdn_cs) as sdn_conn:

        # Load config
        weights = load_weights(out_conn, schema)
        print(f"  {len(weights)} scoring weights loaded.")

        # Load SDN names needed for org token analysis
        print("  Loading SDN entity + AKA names ...")
        org_names, aka_info = load_sdn_names(sdn_conn)
        print(f"  {len(org_names):,} entity names, {len(aka_info):,} AKA entries.")

        # Fetch gate-passing rows for this run
        print("  Fetching GatePassing rows ...")
        gp_rows = out_conn.cursor().execute(f"""
            SELECT GatePassing_ID,
                   Input_Type, SDN_Type, SDN_Name_Type,
                   SDN_UID, SDN_AKA_UID,
                   FN_JW, LN_JW, OrgNM_JW,
                   InputOrgNM,
                   FullAddress_JW, CityCountry_JW, RegionCountry_JW, Country_JW
            FROM   [{schema}].[MatchingResults_GatePassing]
            WHERE  Run_ID = ?
        """, [run_id]).fetchall()
        n_rows = len(gp_rows)
        print(f"  {n_rows:,} rows to score.")

        if not n_rows:
            print("  Nothing to do.")
            return

        _SQL_UPDATE = f"""
            UPDATE [{schema}].[MatchingResults_GatePassing]
               SET MatchDisposition         = ?,
                   Score_NameSimilarity     = ?,
                   Score_NameContext        = ?,
                   Score_EntityType         = ?,
                   Score_Address            = ?,
                   Score_Country            = ?,
                   Score_TokenMatchCategory = ?
             WHERE GatePassing_ID = ?
        """
        cur = out_conn.cursor()
        cur.fast_executemany = True

        batch      = []
        total_done = 0

        for row in gp_rows:
            s = score_row(row, org_names, aka_info, weights)
            batch.append((
                s['Total'],
                s['NameSimilarity'],
                s['NameContext'],
                s['EntityType'],
                s['Address'],
                s['Country'],
                s['TokenMatchCategory'],
                row.GatePassing_ID,
            ))

            if len(batch) >= args.batch_size:
                cur.executemany(_SQL_UPDATE, batch)
                out_conn.commit()
                total_done += len(batch)
                print(f"  {total_done:,} / {n_rows:,} scored ...", end='\r')
                batch = []

        if batch:
            cur.executemany(_SQL_UPDATE, batch)
            out_conn.commit()
            total_done += len(batch)

    print(f"\n  Done. {total_done:,} rows scored and written to MatchingResults_GatePassing.")


if __name__ == '__main__':
    main()
