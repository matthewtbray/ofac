#!/usr/bin/env python3
"""
fact_registration_address_populate.py

Initial population of dbo.Fact_Registration_Address (Type 2 SCD junction table)
from current Principal_Address_ID and Registered_Agent_Address_ID values in
dbo.Fact_Registration.

  Valid_From  = Formation date (from DIM_Date via Formation_Date_ID),
                or '1900-01-01' if Formation_Date_ID is NULL
  Valid_To    = NULL  (all records are current at initial load)
  Is_Current  = 1

Runs Principal addresses first, then Registered Agent addresses.
Safe to re-run -- skips registrations already present in the junction table
for each address type.

Usage
-----
  python fact_registration_address_populate.py [--batch-size N]
                                               [--type {principal,ra,both}]
"""

import argparse
import time
import pyodbc

CONN_STR = (
    "DRIVER={ODBC Driver 17 for SQL Server};"
    "SERVER=.;DATABASE=Registrations_DW;Trusted_Connection=yes;"
)

# ---------------------------------------------------------------------------
# DDL guard
# ---------------------------------------------------------------------------

_DDL = """
IF OBJECT_ID(N'dbo.Fact_Registration_Address', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.Fact_Registration_Address (
        ID              BIGINT      NOT NULL IDENTITY(1,1) PRIMARY KEY,
        Registration_ID BIGINT      NOT NULL,
        Address_ID      BIGINT      NOT NULL,
        Address_Type    VARCHAR(20) NOT NULL,
        Valid_From      DATE        NOT NULL,
        Valid_To        DATE        NULL,
        Is_Current      BIT         NOT NULL DEFAULT 1,

        CONSTRAINT FK_FRA_Registration FOREIGN KEY (Registration_ID)
            REFERENCES dbo.Fact_Registration (ID),
        CONSTRAINT FK_FRA_Address FOREIGN KEY (Address_ID)
            REFERENCES dbo.DIM_Address (ID)
    );
    CREATE INDEX IX_FRA_Registration ON dbo.Fact_Registration_Address (Registration_ID);
    CREATE INDEX IX_FRA_Address      ON dbo.Fact_Registration_Address (Address_ID);
END
"""

# ---------------------------------------------------------------------------
# Insert templates  (TOP batching, safe re-run via NOT EXISTS)
# ---------------------------------------------------------------------------

_INSERT = """
INSERT INTO dbo.Fact_Registration_Address
    (Registration_ID, Address_ID, Address_Type, Valid_From, Valid_To, Is_Current)
SELECT TOP ({batch})
    f.ID,
    {addr_id_col},
    '{addr_type}',
    CAST(GETDATE() AS DATE),
    NULL,
    1
FROM  dbo.Fact_Registration f
WHERE {addr_id_col} IS NOT NULL
  AND NOT EXISTS (
      SELECT 1
      FROM   dbo.Fact_Registration_Address fra
      WHERE  fra.Registration_ID = f.ID
        AND  fra.Address_Type    = '{addr_type}'
  );
"""

CONFIGS = {
    'principal': dict(
        label       = 'Principal',
        addr_id_col = 'f.Principal_Address_ID',
        addr_type   = 'Principal',
    ),
    'ra': dict(
        label       = 'Registered Agent',
        addr_id_col = 'f.Registered_Agent_Address_ID',
        addr_type   = 'Registered Agent',
    ),
}

# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_type(conn, cfg: dict, batch_size: int):
    cur = conn.cursor()

    # Count outstanding rows
    cur.execute(f"""
        SELECT COUNT(*)
        FROM   dbo.Fact_Registration f
        WHERE  {cfg['addr_id_col']} IS NOT NULL
          AND  NOT EXISTS (
              SELECT 1 FROM dbo.Fact_Registration_Address fra
              WHERE  fra.Registration_ID = f.ID
                AND  fra.Address_Type    = '{cfg['addr_type']}'
          )
    """)
    remaining = cur.fetchone()[0]
    print(f"  {cfg['label']}: {remaining:,} rows to insert.")

    if remaining == 0:
        print("  Nothing to do.")
        return

    sql   = _INSERT.format(batch=batch_size, **cfg)
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

    print(f"\n  Done. {total:,} rows in {(time.time()-t0)/60:.1f} min.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--batch-size', type=int, default=100_000)
    ap.add_argument('--type',       choices=['principal', 'ra', 'both'],
                    default='both')
    args = ap.parse_args()

    conn = pyodbc.connect(CONN_STR)
    conn.autocommit = False
    cur  = conn.cursor()

    print("Creating table if needed...")
    cur.execute(_DDL)
    conn.commit()

    types = ['principal', 'ra'] if args.type == 'both' else [args.type]

    for t in types:
        print(f"\n=== {CONFIGS[t]['label']} addresses ===")
        run_type(conn, CONFIGS[t], args.batch_size)

    conn.close()
    print("\nFinished.")


if __name__ == '__main__':
    main()
