#!/usr/bin/env python3
"""
populate_dim_company.py

Initial population of DIM_Company from existing Fact_Registration data,
then updates Fact_Registration.Company_ID.

Matching key: (Original_Formation_Juris_ID, Company_Name_Normalized)
  where Company_Name_Normalized = entity suffix expanded + all non-alnum
  except '#' stripped + uppercased.

Run after:  schema_ddl.py, setup_tracking.py
Run before: incremental_load.py (first run)

Steps
-----
  1. Compute Company_Name_Normalized on Fact_Registration rows where it is NULL.
  2. Insert distinct (Original_Formation_Juris_ID, Company_Name_Normalized)
     combos into DIM_Company.
  3. Update Fact_Registration.Company_ID from DIM_Company.

Usage
-----
  python populate_dim_company.py [--batch-size N]
"""

import argparse
import re
import time
import pyodbc

CONN_STR = (
    "DRIVER={ODBC Driver 17 for SQL Server};"
    "SERVER=.;DATABASE=Registrations_DW;Trusted_Connection=yes;"
)

# ---------------------------------------------------------------------------
# Company name normalization  (same logic as incremental_load.py)
# ---------------------------------------------------------------------------

_WHITESPACE          = re.compile(r'\s+')
_NON_ALNUM_KEEP_HASH = re.compile(r'[^A-Za-z0-9#]+')

ENTITY_SUFFIX_MAP = {
    'LLC':    'Limited Liability Company',
    'LC':     'Limited Company',
    'INC':    'Incorporated',
    'CORP':   'Corporation',
    'LTD':    'Limited',
    'LP':     'Limited Partnership',
    'LLP':    'Limited Liability Partnership',
    'LLLP':   'Limited Liability Limited Partnership',
    'PC':     'Professional Corporation',
    'PLLC':   'Professional Limited Liability Company',
    'PLLLC':  'Professional Limited Liability Limited Company',
    'PA':     'Professional Association',
    'PLC':    'Public Limited Company',
    'CO':     'Company',
    'ASSOC':  'Association',
    'ASSN':   'Association',
    'BROS':   'Brothers',
    'INTL':   'International',
    'NATL':   'National',
    'MGMT':   'Management',
    'SVCS':   'Services',
    'SVC':    'Service',
    'TECH':   'Technology',
}


def normalize_company_name(name: str) -> str:
    if not name:
        return ''
    tokens = _WHITESPACE.split(name.strip())
    tokens = [t for t in tokens if t]
    if not tokens:
        return ''
    last_stripped = re.sub(r'\.', '', tokens[-1]).upper()
    if last_stripped in ENTITY_SUFFIX_MAP:
        tokens[-1] = ENTITY_SUFFIX_MAP[last_stripped]
    joined = ' '.join(tokens)
    return _NON_ALNUM_KEEP_HASH.sub('', joined).upper()


# ---------------------------------------------------------------------------
# DDL guard -- add Company_Name_Normalized column to Fact_Registration
# ---------------------------------------------------------------------------

_ENSURE_NORM_COL = """
IF COL_LENGTH('dbo.Fact_Registration', 'Company_Name_Normalized') IS NULL
    ALTER TABLE dbo.Fact_Registration
        ADD Company_Name_Normalized VARCHAR(1000) NULL;
"""


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

def step1_normalize(conn, batch_size: int):
    """Compute Company_Name_Normalized in Python; write back to Fact_Registration."""
    print("Step 1: Normalizing company names...")
    cur = conn.cursor()
    cur.fast_executemany = True

    cur.execute("""
        SELECT MIN(ID), MAX(ID), COUNT(*)
        FROM   dbo.Fact_Registration
        WHERE  Company_Name_Normalized IS NULL
          AND  Company_Name IS NOT NULL
    """)
    min_id, max_id, total = cur.fetchone()

    if not total:
        print("  Nothing to normalize.")
        return

    print(f"  {total:,} rows to process...")
    updated = 0
    t0      = time.time()
    pos     = min_id

    while pos <= max_id:
        rows = cur.execute("""
            SELECT ID, Company_Name
            FROM   dbo.Fact_Registration
            WHERE  ID BETWEEN ? AND ?
              AND  Company_Name_Normalized IS NULL
              AND  Company_Name IS NOT NULL
        """, [pos, pos + batch_size - 1]).fetchall()

        if rows:
            params = [(normalize_company_name(r[1]), r[0]) for r in rows]
            cur.executemany("""
                UPDATE dbo.Fact_Registration
                SET    Company_Name_Normalized = ?
                WHERE  ID = ?
            """, params)
            conn.commit()
            updated += len(rows)

        elapsed = time.time() - t0
        rps     = updated / elapsed if elapsed > 0 else 0
        eta_min = (total - updated) / rps / 60 if rps > 0 else 0
        print(
            f"  {updated:,} / {total:,}  {rps:,.0f} rows/sec  ETA {eta_min:.0f} min   ",
            end='\r'
        )
        pos += batch_size

    print(f"\n  Done. {updated:,} rows in {(time.time()-t0)/60:.1f} min.")


def step2_insert_dim_company(conn):
    """Insert distinct (Original_Formation_Juris_ID, Company_Name_Normalized) into DIM_Company."""
    print("Step 2: Inserting DIM_Company rows...")
    cur = conn.cursor()
    t0  = time.time()

    cur.execute("""
        INSERT INTO dbo.DIM_Company
            (Original_Formation_Juris_ID, Company_Name, Company_Name_Normalized)
        SELECT
            f.Original_Formation_Juris_ID,
            MIN(f.Company_Name),
            f.Company_Name_Normalized
        FROM  dbo.Fact_Registration f
        WHERE f.Original_Formation_Juris_ID IS NOT NULL
          AND f.Company_Name_Normalized     IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM dbo.DIM_Company c
              WHERE  c.Original_Formation_Juris_ID = f.Original_Formation_Juris_ID
                AND  c.Company_Name_Normalized     = f.Company_Name_Normalized
          )
        GROUP BY f.Original_Formation_Juris_ID, f.Company_Name_Normalized
    """)
    inserted = cur.rowcount
    conn.commit()
    print(f"  {inserted:,} DIM_Company rows inserted in {time.time()-t0:.1f}s.")


def step3_update_company_id(conn, batch_size: int):
    """Batch-update Fact_Registration.Company_ID from DIM_Company."""
    print("Step 3: Updating Fact_Registration.Company_ID...")
    cur = conn.cursor()

    cur.execute("""
        SELECT COUNT(*)
        FROM   dbo.Fact_Registration f
        WHERE  f.Company_ID IS NULL
          AND  f.Original_Formation_Juris_ID IS NOT NULL
          AND  f.Company_Name_Normalized     IS NOT NULL
    """)
    remaining = cur.fetchone()[0]
    print(f"  {remaining:,} rows to update.")

    if remaining == 0:
        print("  Nothing to do.")
        return

    sql = f"""
        UPDATE TOP ({batch_size}) f
        SET    f.Company_ID = c.ID
        FROM   dbo.Fact_Registration f
        JOIN   dbo.DIM_Company c
            ON  c.Original_Formation_Juris_ID = f.Original_Formation_Juris_ID
            AND c.Company_Name_Normalized     = f.Company_Name_Normalized
        WHERE  f.Company_ID IS NULL
    """
    total = 0
    t0    = time.time()

    while True:
        cur.execute(sql)
        n = cur.rowcount
        conn.commit()
        if n == 0:
            break
        total  += n
        elapsed = time.time() - t0
        rps     = total / elapsed if elapsed > 0 else 0
        eta_min = (remaining - total) / rps / 60 if rps > 0 else 0
        print(
            f"  {total:,} / {remaining:,}  {rps:,.0f} rows/sec  ETA {eta_min:.0f} min   ",
            end='\r'
        )

    print(f"\n  Done. {total:,} rows in {(time.time()-t0)/60:.1f} min.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--batch-size', type=int, default=50_000)
    args = ap.parse_args()

    conn = pyodbc.connect(CONN_STR)
    conn.autocommit = False
    cur  = conn.cursor()

    print("Adding Company_Name_Normalized column if needed...")
    cur.execute(_ENSURE_NORM_COL)
    conn.commit()

    step1_normalize(conn, args.batch_size)
    step2_insert_dim_company(conn)
    step3_update_company_id(conn, args.batch_size)

    conn.close()
    print("\nFinished.")


if __name__ == '__main__':
    main()
