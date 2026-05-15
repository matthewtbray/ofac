#!/usr/bin/env python3
"""
populate_lookups.py

Populates DIM_Entity_Type and DIM_Status from distinct values already
present in Fact_Registration, then updates the FK columns on Fact_Registration.

Run this after schema_ddl.py and before populate_dim_company.py.

Two passes per table:
  1. INSERT distinct raw codes not already in the lookup table.
     Entity_Type_Name / Status_Description default to the raw code value --
     edit the lookup tables in SSMS afterward to add canonical descriptions.
  2. UPDATE Fact_Registration FKs from the lookup table.

Usage
-----
  python populate_lookups.py
"""

import time
import pyodbc

CONN_STR = (
    "DRIVER={ODBC Driver 17 for SQL Server};"
    "SERVER=.;DATABASE=Registrations_DW;Trusted_Connection=yes;"
)

# ---------------------------------------------------------------------------
# Known canonical mappings  (extend as needed)
# These seed readable descriptions rather than leaving them as raw codes.
# ---------------------------------------------------------------------------

ENTITY_TYPE_SEEDS = {
    # Code                  : (Name,                                    Category)
    'LLC'                   : ('Limited Liability Company',             'LLC'),
    'CORP'                  : ('Corporation',                           'Corporation'),
    'INC'                   : ('Corporation',                           'Corporation'),
    'LP'                    : ('Limited Partnership',                   'Partnership'),
    'LLP'                   : ('Limited Liability Partnership',         'Partnership'),
    'LLLP'                  : ('Limited Liability Limited Partnership', 'Partnership'),
    'PC'                    : ('Professional Corporation',              'Corporation'),
    'PLLC'                  : ('Professional Limited Liability Company','LLC'),
    'PA'                    : ('Professional Association',              'Other'),
    'TRUST'                 : ('Trust',                                 'Trust'),
    'NPC'                   : ('Nonprofit Corporation',                 'Nonprofit'),
    'COOP'                  : ('Cooperative',                           'Other'),
    'GP'                    : ('General Partnership',                   'Partnership'),
}

STATUS_SEEDS = {
    # Code          : (Description,                    Is_Active)
    'ACTIVE'        : ('Active',                        1),
    'GOOD STANDING' : ('Active - Good Standing',        1),
    'INACTIVE'      : ('Inactive',                      0),
    'DISSOLVED'     : ('Dissolved',                     0),
    'REVOKED'       : ('Revoked',                       0),
    'SUSPENDED'     : ('Suspended',                     0),
    'WITHDRAWN'     : ('Withdrawn',                     0),
    'CANCELLED'     : ('Cancelled',                     0),
    'FORFEITED'     : ('Forfeited',                     0),
    'EXPIRED'       : ('Expired',                       0),
    'MERGED'        : ('Merged',                        0),
    'CONVERTED'     : ('Converted',                     0),
    'DELINQUENT'    : ('Delinquent',                    1),
    'PENDING'       : ('Pending',                       0),
}


# ---------------------------------------------------------------------------
# Entity Type
# ---------------------------------------------------------------------------

def populate_entity_types(conn):
    print("=== DIM_Entity_Type ===")
    cur = conn.cursor()
    t0  = time.time()

    # Collect distinct raw codes from Fact_Registration
    cur.execute("""
        SELECT DISTINCT Entity_Type_State
        FROM   dbo.Fact_Registration
        WHERE  Entity_Type_State IS NOT NULL
          AND  NOT EXISTS (
              SELECT 1 FROM dbo.DIM_Entity_Type e
              WHERE  e.Entity_Type_Code = Entity_Type_State
          )
    """)
    new_codes = [r[0] for r in cur.fetchall()]

    if new_codes:
        params = []
        for code in new_codes:
            upper = code.strip().upper()
            if upper in ENTITY_TYPE_SEEDS:
                name, category = ENTITY_TYPE_SEEDS[upper]
            else:
                name, category = code, None
            params.append((code, name, category))

        cur.executemany("""
            INSERT INTO dbo.DIM_Entity_Type (Entity_Type_Code, Entity_Type_Name, Entity_Type_Category)
            VALUES (?, ?, ?)
        """, params)
        conn.commit()
        print(f"  Inserted {len(params):,} entity type rows.")
    else:
        print("  No new entity type codes found.")

    # Update Fact_Registration.Entity_Type_ID
    cur.execute("""
        UPDATE f
        SET    f.Entity_Type_ID = e.ID
        FROM   dbo.Fact_Registration f
        JOIN   dbo.DIM_Entity_Type e ON e.Entity_Type_Code = f.Entity_Type_State
        WHERE  f.Entity_Type_ID IS NULL
    """)
    updated = cur.rowcount
    conn.commit()
    print(f"  Updated Entity_Type_ID on {updated:,} Fact_Registration rows.  ({time.time()-t0:.1f}s)")


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

def populate_statuses(conn):
    print("\n=== DIM_Status ===")
    cur = conn.cursor()
    t0  = time.time()

    cur.execute("""
        SELECT DISTINCT Status_State
        FROM   dbo.Fact_Registration
        WHERE  Status_State IS NOT NULL
          AND  NOT EXISTS (
              SELECT 1 FROM dbo.DIM_Status s
              WHERE  s.Status_Code = Status_State
          )
    """)
    new_codes = [r[0] for r in cur.fetchall()]

    if new_codes:
        params = []
        for code in new_codes:
            upper = code.strip().upper()
            if upper in STATUS_SEEDS:
                description, is_active = STATUS_SEEDS[upper]
            else:
                description, is_active = code, 0
            params.append((code, description, is_active))

        cur.executemany("""
            INSERT INTO dbo.DIM_Status (Status_Code, Status_Description, Is_Active)
            VALUES (?, ?, ?)
        """, params)
        conn.commit()
        print(f"  Inserted {len(params):,} status rows.")
    else:
        print("  No new status codes found.")

    # Update Fact_Registration.Status_ID
    cur.execute("""
        UPDATE f
        SET    f.Status_ID = s.ID
        FROM   dbo.Fact_Registration f
        JOIN   dbo.DIM_Status s ON s.Status_Code = f.Status_State
        WHERE  f.Status_ID IS NULL
    """)
    updated = cur.rowcount
    conn.commit()
    print(f"  Updated Status_ID on {updated:,} Fact_Registration rows.  ({time.time()-t0:.1f}s)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    conn = pyodbc.connect(CONN_STR)
    conn.autocommit = False

    populate_entity_types(conn)
    populate_statuses(conn)

    conn.close()
    print("\nFinished.  Review DIM_Entity_Type and DIM_Status in SSMS and "
          "update any descriptions or categories that defaulted to the raw code.")


if __name__ == '__main__':
    main()
