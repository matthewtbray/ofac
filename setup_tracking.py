#!/usr/bin/env python3
"""
setup_tracking.py

One-time setup for change-tracking infrastructure:
  1. Adds Record_Insert_Date / Record_Update_Date audit columns to Fact_Registration.
  2. Creates DIM_Registration (Type 2 SCD) if it does not exist.
  3. Does an initial population of DIM_Registration from current Fact_Registration.

Safe to re-run -- DDL is guarded with IF NOT EXISTS / IF COL_LENGTH checks,
and the initial population uses NOT EXISTS to skip already-loaded rows.

Usage
-----
  python setup_tracking.py [--batch-size N]
"""

import argparse
import time
import pyodbc

CONN_STR = (
    "DRIVER={ODBC Driver 17 for SQL Server};"
    "SERVER=.;DATABASE=Registrations_DW;Trusted_Connection=yes;"
)

# ---------------------------------------------------------------------------
# Step 1 -- audit columns on Fact_Registration
# ---------------------------------------------------------------------------

_AUDIT_COLS = """
IF COL_LENGTH('dbo.Fact_Registration', 'Record_Insert_Date') IS NULL
    ALTER TABLE dbo.Fact_Registration
        ADD Record_Insert_Date DATETIME NULL;

IF COL_LENGTH('dbo.Fact_Registration', 'Record_Update_Date') IS NULL
    ALTER TABLE dbo.Fact_Registration
        ADD Record_Update_Date DATETIME NULL;
"""

_AUDIT_BACKFILL = """
UPDATE dbo.Fact_Registration
SET    Record_Insert_Date = GETDATE(),
       Record_Update_Date = GETDATE()
WHERE  Record_Insert_Date IS NULL;
"""


# ---------------------------------------------------------------------------
# Step 2 -- DIM_Registration DDL
# ---------------------------------------------------------------------------

_DIM_REG_DDL = """
IF OBJECT_ID(N'dbo.DIM_Registration', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.DIM_Registration (
        ID                          BIGINT   NOT NULL IDENTITY(1,1) PRIMARY KEY,
        Registration_ID             BIGINT   NOT NULL,
        Company_Name                NVARCHAR(500) NULL,
        Status_State                VARCHAR(100)  NULL,
        Entity_Type_State           VARCHAR(100)  NULL,
        Registered_Agent_Name       NVARCHAR(500) NULL,
        Principal_Address_ID        BIGINT        NULL,
        Registered_Agent_Address_ID BIGINT        NULL,
        Valid_From                  DATE     NOT NULL,
        Valid_To                    DATE     NULL,
        Is_Current                  BIT      NOT NULL DEFAULT 1,

        CONSTRAINT FK_DR_Registration FOREIGN KEY (Registration_ID)
            REFERENCES dbo.Fact_Registration (ID),
        CONSTRAINT FK_DR_PrincipalAddr FOREIGN KEY (Principal_Address_ID)
            REFERENCES dbo.DIM_Address (ID),
        CONSTRAINT FK_DR_RAAddr FOREIGN KEY (Registered_Agent_Address_ID)
            REFERENCES dbo.DIM_Address (ID)
    );

    CREATE INDEX IX_DR_Registration ON dbo.DIM_Registration (Registration_ID);
    CREATE INDEX IX_DR_IsCurrent    ON dbo.DIM_Registration (Is_Current);
END
"""

# ---------------------------------------------------------------------------
# Step 3 -- initial population (batch TOP loop)
# ---------------------------------------------------------------------------

_INSERT_DIM_REG = """
INSERT INTO dbo.DIM_Registration
    (Registration_ID, Company_Name, Status_State, Entity_Type_State,
     Registered_Agent_Name, Principal_Address_ID, Registered_Agent_Address_ID,
     Valid_From, Valid_To, Is_Current)
SELECT TOP ({batch})
    f.ID,
    f.Company_Name,
    f.Status_State,
    f.Entity_Type_State,
    f.Registered_Agent_Name,
    f.Principal_Address_ID,
    f.Registered_Agent_Address_ID,
    CAST(ISNULL(f.Record_Insert_Date, GETDATE()) AS DATE),
    NULL,
    1
FROM  dbo.Fact_Registration f
WHERE NOT EXISTS (
    SELECT 1
    FROM   dbo.DIM_Registration dr
    WHERE  dr.Registration_ID = f.ID
      AND  dr.Is_Current      = 1
);
"""

_COUNT_OUTSTANDING = """
SELECT COUNT(*)
FROM   dbo.Fact_Registration f
WHERE  NOT EXISTS (
    SELECT 1
    FROM   dbo.DIM_Registration dr
    WHERE  dr.Registration_ID = f.ID
      AND  dr.Is_Current      = 1
);
"""


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def step1_audit_cols(conn):
    print("Step 1: Adding audit columns to Fact_Registration...")
    cur = conn.cursor()
    for stmt in _AUDIT_COLS.strip().split(';'):
        stmt = stmt.strip()
        if stmt:
            cur.execute(stmt)
    conn.commit()

    print("  Backfilling NULL audit dates...")
    t0 = time.time()
    cur.execute(_AUDIT_BACKFILL)
    n = cur.rowcount
    conn.commit()
    print(f"  {n:,} rows backfilled in {time.time()-t0:.1f}s.")


def step2_dim_reg_ddl(conn):
    print("Step 2: Creating DIM_Registration if needed...")
    cur = conn.cursor()
    cur.execute(_DIM_REG_DDL)
    conn.commit()
    print("  Done.")


def step3_initial_population(conn, batch_size: int):
    print("Step 3: Populating DIM_Registration (initial load)...")
    cur = conn.cursor()

    cur.execute(_COUNT_OUTSTANDING)
    remaining = cur.fetchone()[0]
    print(f"  {remaining:,} rows to insert.")

    if remaining == 0:
        print("  Nothing to do.")
        return

    sql   = _INSERT_DIM_REG.format(batch=batch_size)
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
            f"  {total:,} / {remaining:,}  "
            f"{rps:,.0f} rows/sec  ETA {eta_min:.0f} min   ",
            end='\r'
        )

    print(f"\n  Done. {total:,} rows in {(time.time()-t0)/60:.1f} min.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--batch-size', type=int, default=50_000)
    args = ap.parse_args()

    conn = pyodbc.connect(CONN_STR)
    conn.autocommit = False

    step1_audit_cols(conn)
    step2_dim_reg_ddl(conn)
    step3_initial_population(conn, args.batch_size)

    conn.close()
    print("\nFinished.")


if __name__ == '__main__':
    main()
