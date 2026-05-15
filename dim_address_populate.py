#!/usr/bin/env python3
"""
dim_address_populate.py

Populates DIM_Address and updates address FK columns in Fact_Registration.

Phases
------
  pr   Insert distinct Principal address NWS combos into DIM_Address
       (is_PR_Address = 1).  Update existing DIM_Address rows that match
       a principal address.  Update Fact_Registration.Principal_Address_ID.

  ra   Insert distinct Registered Agent NWS combos into DIM_Address
       (is_RA_Address = 1).  Update existing DIM_Address rows that match
       an RA address.  Update Fact_Registration.Registered_Agent_Address_ID.

Usage
-----
  python dim_address_populate.py [--phase {pr,ra,both}] [--batch-size N]
"""

import argparse
import time
import pyodbc

CONN_STR = (
    "DRIVER={ODBC Driver 17 for SQL Server};"
    "SERVER=.;DATABASE=Registrations_DW;Trusted_Connection=yes;"
)

# ---------------------------------------------------------------------------
# Source configurations
# ---------------------------------------------------------------------------

PHASE_CONFIGS = {
    'pr': dict(
        label        = 'Principal',
        nws1_col     = 'Principal_Address_1_NWS',
        nws2_col     = 'Principal_Address_2_NWS',
        csz_col      = 'Address_CSZ_NWS',
        addr1_raw    = 'Principal_Address_1',
        addr2_raw    = 'Principal_Address_2',
        city_raw     = 'Principal_City',
        state_raw    = 'Principal_State',
        postal_raw   = 'Principal_Postal_Code',
        country_raw  = 'Principal_Country',
        flag_col     = 'is_PR_Address',
        fk_col       = 'Principal_Address_ID',
    ),
    'ra': dict(
        label        = 'Registered Agent',
        nws1_col     = 'RA_Address_1_NWS',
        nws2_col     = 'RA_Address_2_NWS',
        csz_col      = 'RA_CSZ_NWS',
        addr1_raw    = 'Registered_Agent_Street_Address_1',
        addr2_raw    = 'Registered_Agent_Street_Address_2',
        city_raw     = 'Registered_Agent_City',
        state_raw    = 'Registered_Agent_State',
        postal_raw   = 'Registered_Agent_Postal_Code',
        country_raw  = 'Registered_Agent_Country',
        flag_col     = 'is_RA_Address',
        fk_col       = 'Registered_Agent_Address_ID',
    ),
}


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def ensure_schema(conn):
    """Add flag columns to DIM_Address and index on Address_NWS if missing."""
    cur = conn.cursor()
    for col in ('is_PR_Address', 'is_RA_Address'):
        cur.execute(
            f"IF COL_LENGTH('dbo.DIM_Address', '{col}') IS NULL "
            f"ALTER TABLE dbo.DIM_Address ADD [{col}] BIT NULL"
        )
    cur.execute("""
        IF NOT EXISTS (
            SELECT 1 FROM sys.indexes
            WHERE  object_id = OBJECT_ID(N'dbo.DIM_Address')
              AND  name       = N'IX_DIM_Address_NWS'
        )
        CREATE INDEX IX_DIM_Address_NWS ON dbo.DIM_Address (Address_NWS);
    """)
    conn.commit()
    print("Schema ready.")


# ---------------------------------------------------------------------------
# Insert new DIM_Address rows
# ---------------------------------------------------------------------------

def insert_dim(conn, cfg: dict):
    """Insert distinct NWS combos not already in DIM_Address, flag = 1."""
    c = cfg
    sql = f"""
        INSERT INTO dbo.DIM_Address
            (Address_NWS,
             Street_Address_1, Street_Address_2,
             City, [State], Postal_code, Country,
             {c['flag_col']})
        SELECT
            nws_key,
            MIN({c['addr1_raw']}),
            MIN({c['addr2_raw']}),
            MIN({c['city_raw']}),
            MIN({c['state_raw']}),
            MIN({c['postal_raw']}),
            MIN({c['country_raw']}),
            1
        FROM (
            SELECT
                ISNULL({c['nws1_col']}, '')
                    + ISNULL({c['nws2_col']}, '')
                    + ISNULL({c['csz_col']},  '')     AS nws_key,
                {c['addr1_raw']}, {c['addr2_raw']},
                {c['city_raw']},  {c['state_raw']},
                {c['postal_raw']}, {c['country_raw']}
            FROM dbo.Fact_Registration
            WHERE {c['nws1_col']} IS NOT NULL
               OR {c['nws2_col']} IS NOT NULL
               OR {c['csz_col']}  IS NOT NULL
        ) src
        WHERE NOT EXISTS (
            SELECT 1 FROM dbo.DIM_Address d
            WHERE  d.Address_NWS = src.nws_key
        )
        GROUP BY nws_key;
    """
    cur = conn.cursor()
    print(f"  Inserting new DIM_Address rows ({cfg['label']})...")
    t0 = time.time()
    cur.execute(sql)
    inserted = cur.rowcount
    conn.commit()
    print(f"  {inserted:,} rows inserted in {time.time()-t0:.1f}s.")


# ---------------------------------------------------------------------------
# Flag existing DIM_Address rows
# ---------------------------------------------------------------------------

def flag_existing(conn, cfg: dict):
    """Set flag = 1 on DIM_Address rows that match this source but were
    inserted by a previous phase (flag is currently NULL or 0)."""
    c = cfg
    sql = f"""
        UPDATE d
        SET    d.{c['flag_col']} = 1
        FROM   dbo.DIM_Address d
        WHERE  d.{c['flag_col']} IS NULL
          AND  EXISTS (
              SELECT 1
              FROM   dbo.Fact_Registration f
              WHERE  d.Address_NWS = ISNULL(f.{c['nws1_col']},'')
                                   + ISNULL(f.{c['nws2_col']},'')
                                   + ISNULL(f.{c['csz_col']}, '')
          );
    """
    cur = conn.cursor()
    print(f"  Flagging existing DIM_Address rows ({cfg['label']})...")
    t0 = time.time()
    cur.execute(sql)
    flagged = cur.rowcount
    conn.commit()
    print(f"  {flagged:,} existing rows flagged in {time.time()-t0:.1f}s.")


# ---------------------------------------------------------------------------
# Update Fact_Registration FK
# ---------------------------------------------------------------------------

def update_fk(conn, cfg: dict, batch_size: int):
    """Batch-update Fact_Registration FK column from DIM_Address.ID."""
    c   = cfg
    sql = f"""
        UPDATE TOP ({batch_size}) f
        SET    f.{c['fk_col']} = d.ID
        FROM   dbo.Fact_Registration f
        JOIN   dbo.DIM_Address d
            ON d.Address_NWS = ISNULL(f.{c['nws1_col']},'')
                             + ISNULL(f.{c['nws2_col']},'')
                             + ISNULL(f.{c['csz_col']}, '')
        WHERE  f.{c['fk_col']} IS NULL
          AND (f.{c['nws1_col']} IS NOT NULL
               OR f.{c['nws2_col']} IS NOT NULL
               OR f.{c['csz_col']}  IS NOT NULL);
    """
    cur = conn.cursor()

    cur.execute(
        f"SELECT COUNT(*) FROM dbo.Fact_Registration "
        f"WHERE {c['fk_col']} IS NULL "
        f"  AND ({c['nws1_col']} IS NOT NULL "
        f"       OR {c['nws2_col']} IS NOT NULL "
        f"       OR {c['csz_col']}  IS NOT NULL)"
    )
    remaining = cur.fetchone()[0]
    print(f"  Updating {c['fk_col']}: {remaining:,} rows to process...")

    total = 0
    t0    = time.time()
    while True:
        cur.execute(sql)
        n = cur.rowcount
        conn.commit()
        if n == 0:
            break
        total   += n
        elapsed  = time.time() - t0
        rps      = total / elapsed if elapsed > 0 else 0
        eta_min  = (remaining - total) / rps / 60 if rps > 0 else 0
        print(
            f"  {total:,} / {remaining:,}  "
            f"{rps:,.0f} rows/sec  ETA {eta_min:.0f} min   ",
            end='\r'
        )

    print(f"\n  Done. {total:,} rows updated in {(time.time()-t0)/60:.1f} min.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--phase',      choices=['pr', 'ra', 'both'], default='both')
    ap.add_argument('--batch-size', type=int, default=100_000)
    args = ap.parse_args()

    conn = pyodbc.connect(CONN_STR)
    conn.autocommit = False

    ensure_schema(conn)

    phases = ['pr', 'ra'] if args.phase == 'both' else [args.phase]

    for phase in phases:
        cfg = PHASE_CONFIGS[phase]
        print(f"\n=== {cfg['label']} addresses ===")
        insert_dim(conn, cfg)
        flag_existing(conn, cfg)
        update_fk(conn, cfg, args.batch_size)

    conn.close()
    print("\nFinished.")


if __name__ == '__main__':
    main()
