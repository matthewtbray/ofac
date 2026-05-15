#!/usr/bin/env python3
"""
nws_populate.py

Populates NWS (No White Space) address columns in dbo.Fact_Registration.

Sources
-------
  principal  (default)
      Reads : Principal_Address_1, Principal_Address_2,
              Principal_City, Principal_State, Principal_Postal_Code
      Writes: Principal_Address_1_NWS, Principal_Address_2_NWS, Address_CSZ_NWS

  ra
      Reads : Registered_Agent_Street_Address_1, Registered_Agent_Street_Address_2,
              Registered_Agent_City, Registered_Agent_State, Registered_Agent_Postal_Code
      Writes: RA_Address_1_NWS, RA_Address_2_NWS, RA_CSZ_NWS

Usage
-----
  python nws_populate.py [--source {principal,ra,both}]
                         [--batch-size N]
                         [--resume]
"""

import argparse
import re
import sys
import time
import pyodbc
from collections import defaultdict

CONN_STR = (
    "DRIVER={ODBC Driver 17 for SQL Server};"
    "SERVER=.;DATABASE=Registrations_DW;Trusted_Connection=yes;"
)

# ---------------------------------------------------------------------------
# Source configurations
# ---------------------------------------------------------------------------

SOURCE_CONFIGS = {
    'principal': dict(
        addr1_raw  = 'Principal_Address_1',
        addr2_raw  = 'Principal_Address_2',
        city_raw   = 'Principal_City',
        state_raw  = 'Principal_State',
        postal_raw = 'Principal_Postal_Code',
        nws1_col   = 'Principal_Address_1_NWS',
        nws2_col   = 'Principal_Address_2_NWS',
        csz_col    = 'Address_CSZ_NWS',
    ),
    'ra': dict(
        addr1_raw  = 'Registered_Agent_Street_Address_1',
        addr2_raw  = 'Registered_Agent_Street_Address_2',
        city_raw   = 'Registered_Agent_City',
        state_raw  = 'Registered_Agent_State',
        postal_raw = 'Registered_Agent_Postal_Code',
        nws1_col   = 'RA_Address_1_NWS',
        nws2_col   = 'RA_Address_2_NWS',
        csz_col    = 'RA_CSZ_NWS',
    ),
}

# ---------------------------------------------------------------------------
# String helpers
# ---------------------------------------------------------------------------

_PUNCT_TO_SPACE = re.compile(r'[.,;\-/\\]+')
_WHITESPACE     = re.compile(r'\s+')
_NON_ALNUM      = re.compile(r'[^A-Za-z0-9]+')


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
        am[k].sort(key=lambda x: x[1])   # Skip=0 first
    am['#'] = [('Number', 0)]
    return dict(am)


def _expand_token(token_upper: str, is_first_alpha: bool, am: dict):
    mappings = am.get(token_upper)
    if not mappings:
        return None
    if len(mappings) == 1:
        return mappings[0][0]
    if is_first_alpha:
        return mappings[0][0]
    for full, skip in mappings:
        if skip == 1:
            return full
    return mappings[0][0]


def make_nws(raw, am: dict):
    if not raw:
        return None
    s = _PUNCT_TO_SPACE.sub(' ', raw)
    tokens = _WHITESPACE.split(s.strip())
    first_alpha_idx = next(
        (i for i, t in enumerate(tokens) if t and re.search(r'[A-Za-z]', t)),
        None
    )
    result = []
    for i, token in enumerate(tokens):
        if not token:
            continue
        expanded = _expand_token(token.upper(), i == first_alpha_idx, am)
        result.append(expanded if expanded is not None else token)
    return _NON_ALNUM.sub('', ''.join(result)) or None


def make_csz_nws(city, state, postal, am: dict):
    city_nws   = make_nws(city, am) or ''
    state_nws  = _NON_ALNUM.sub('', state  or '')
    postal_nws = _NON_ALNUM.sub('', (postal or '')[:5])
    return (city_nws + state_nws + postal_nws) or None


# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

def ensure_columns(conn, cfg: dict):
    cur = conn.cursor()
    for col in (cfg['nws1_col'], cfg['nws2_col'], cfg['csz_col']):
        size = 'VARCHAR(200)' if col == cfg['csz_col'] else 'VARCHAR(500)'
        cur.execute(
            f"IF COL_LENGTH('dbo.Fact_Registration', '{col}') IS NULL "
            f"ALTER TABLE dbo.Fact_Registration ADD [{col}] {size} NULL"
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Processing
# ---------------------------------------------------------------------------

def run_source(conn, cfg: dict, batch_size: int, resume: bool):
    cur = conn.cursor()
    ensure_columns(conn, cfg)

    print(f"  Loading abbreviation map...")
    am = load_abbrev_map(conn)

    cur.execute("SELECT MIN(ID), MAX(ID), COUNT(*) FROM dbo.Fact_Registration")
    min_id, max_id, total_rows = cur.fetchone()

    resume_filter = f"AND {cfg['nws1_col']} IS NULL" if resume else ""

    select_sql = f"""
        SELECT ID,
               {cfg['addr1_raw']}, {cfg['addr2_raw']},
               {cfg['city_raw']},  {cfg['state_raw']}, {cfg['postal_raw']}
        FROM   dbo.Fact_Registration
        WHERE  ID BETWEEN ? AND ?
               {resume_filter}
    """

    update_sql = f"""
        UPDATE dbo.Fact_Registration
        SET    {cfg['nws1_col']} = ?,
               {cfg['nws2_col']} = ?,
               {cfg['csz_col']}  = ?
        WHERE  ID = ?
    """
    cur.fast_executemany = True

    updated     = 0
    t0          = time.time()
    batch_start = min_id

    while batch_start <= max_id:
        batch_end = batch_start + batch_size - 1
        rows = cur.execute(select_sql, [batch_start, batch_end]).fetchall()

        if rows:
            params = [
                (
                    make_nws(r[1], am),
                    make_nws(r[2], am),
                    make_csz_nws(r[3], r[4], r[5], am),
                    r[0],
                )
                for r in rows
            ]
            cur.executemany(update_sql, params)
            conn.commit()
            updated += len(rows)

        elapsed = time.time() - t0
        rps     = updated / elapsed if elapsed > 0 else 0
        eta_min = (total_rows - updated) / rps / 60 if rps > 0 else 0
        print(
            f"  {updated:,} rows  {rps:,.0f} rows/sec  ETA {eta_min:.0f} min   ",
            end='\r'
        )
        batch_start = batch_end + 1

    print(f"\n  Done. {updated:,} rows in {(time.time()-t0)/60:.1f} min.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--source',     choices=['principal', 'ra', 'both'], default='principal')
    ap.add_argument('--batch-size', type=int, default=100_000)
    ap.add_argument('--resume',     action='store_true',
                    help='Skip rows where the first NWS column is already populated')
    args = ap.parse_args()

    conn = pyodbc.connect(CONN_STR)
    conn.autocommit = False

    sources = ['principal', 'ra'] if args.source == 'both' else [args.source]

    for src in sources:
        print(f"\n=== Source: {src} ===")
        run_source(conn, SOURCE_CONFIGS[src], args.batch_size, args.resume)

    conn.close()
    print("\nFinished.")


if __name__ == '__main__':
    main()
