#!/usr/bin/env python3
"""
incremental_load.py

Full incremental load pipeline for Registrations_DW.

Reads from dbo.Staging_Registration (and dbo.Staging_Registration_Principal
for principal data), then executes the following phases in order:

  1. nws         Compute NWS columns for PR and RA addresses in staging.
  2. address     Upsert new address combos into DIM_Address; write FKs back to staging.
  3. company     Normalize company names; upsert DIM_Company; write Company_ID back.
  4. fact        Upsert Fact_Registration (INSERT new, UPDATE changed).
                 Resolves Status_ID and Entity_Type_ID from lookup tables.
  5. dim_reg     Type 2 SCD on DIM_Registration.
  6. fra         Type 2 SCD on Fact_Registration_Address.
  7. principal   Type 2 SCD on DIM_Registration_Principal (individual-level).

Natural business key: (Juris_ID, Juris_ID_Number)  -- composite throughout.

Staging_Registration_Principal expected columns
-----------------------------------------------
  Juris_ID, Juris_ID_Number   -- link back to the registration
  Title, Name_Prefix, First_Name, Middle_Name, Last_Name, Name_Suffix

Usage
-----
  python incremental_load.py [--batch-size N]
                             [--phase {all,nws,address,company,fact,dim_reg,fra,principal}]
                             [--dry-run]
"""

import argparse
import re
import time
import pyodbc
from collections import defaultdict

CONN_STR = (
    "DRIVER={ODBC Driver 17 for SQL Server};"
    "SERVER=.;DATABASE=Registrations_DW;Trusted_Connection=yes;"
)

# ---------------------------------------------------------------------------
# NWS helpers
# ---------------------------------------------------------------------------

_PUNCT_TO_SPACE       = re.compile(r'[.,;\-/\\]+')
_WHITESPACE           = re.compile(r'\s+')
_NON_ALNUM            = re.compile(r'[^A-Za-z0-9]+')
_NON_ALNUM_KEEP_HASH  = re.compile(r'[^A-Za-z0-9#]+')


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
# Company name normalization
# ---------------------------------------------------------------------------

# Maps stripped/uppercased last token → canonical long form.
# Periods are stripped before lookup so "L.L.C." matches "LLC".
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
    """Expand entity suffix, strip all non-alphanumeric except #, uppercase."""
    if not name:
        return ''
    tokens = _WHITESPACE.split(name.strip())
    tokens = [t for t in tokens if t]
    if not tokens:
        return ''
    # Strip periods from last token and look up in suffix map
    last_stripped = re.sub(r'\.', '', tokens[-1]).upper()
    if last_stripped in ENTITY_SUFFIX_MAP:
        tokens[-1] = ENTITY_SUFFIX_MAP[last_stripped]
    joined = ' '.join(tokens)
    return _NON_ALNUM_KEEP_HASH.sub('', joined).upper()


# ---------------------------------------------------------------------------
# Phase 1 -- NWS columns in Staging_Registration
# ---------------------------------------------------------------------------

_STAGING_NWS_CFGS = {
    'pr': dict(
        addr1_raw='Principal_Address_1',    addr2_raw='Principal_Address_2',
        city_raw='Principal_City',          state_raw='Principal_State',
        postal_raw='Principal_Postal_Code',
        nws1_col='Principal_Address_1_NWS', nws2_col='Principal_Address_2_NWS',
        csz_col='Address_CSZ_NWS',
    ),
    'ra': dict(
        addr1_raw='Registered_Agent_Street_Address_1',
        addr2_raw='Registered_Agent_Street_Address_2',
        city_raw='Registered_Agent_City',   state_raw='Registered_Agent_State',
        postal_raw='Registered_Agent_Postal_Code',
        nws1_col='RA_Address_1_NWS',        nws2_col='RA_Address_2_NWS',
        csz_col='RA_CSZ_NWS',
    ),
}


def phase_nws(conn, batch_size: int):
    print("\n=== Phase 1: NWS columns ===")
    cur = conn.cursor()
    cur.fast_executemany = True

    print("  Loading abbreviation map...")
    am = load_abbrev_map(conn)

    for key, cfg in _STAGING_NWS_CFGS.items():
        label = 'Principal' if key == 'pr' else 'Registered Agent'
        rows = cur.execute(f"""
            SELECT Staging_ID,
                   {cfg['addr1_raw']}, {cfg['addr2_raw']},
                   {cfg['city_raw']},  {cfg['state_raw']}, {cfg['postal_raw']}
            FROM   dbo.Staging_Registration
            WHERE  {cfg['nws1_col']} IS NULL
        """).fetchall()

        if not rows:
            print(f"  {label}: nothing to do.")
            continue

        params = [
            (make_nws(r[1], am), make_nws(r[2], am),
             make_csz_nws(r[3], r[4], r[5], am), r[0])
            for r in rows
        ]
        cur.executemany(f"""
            UPDATE dbo.Staging_Registration
            SET    {cfg['nws1_col']} = ?,
                   {cfg['nws2_col']} = ?,
                   {cfg['csz_col']}  = ?
            WHERE  Staging_ID = ?
        """, params)
        conn.commit()
        print(f"  {label}: {len(params):,} rows updated.")


# ---------------------------------------------------------------------------
# Phase 2 -- DIM_Address upsert
# ---------------------------------------------------------------------------

_DIM_ADDR_CFGS = {
    'pr': dict(
        label='Principal',
        nws1_col='Principal_Address_1_NWS', nws2_col='Principal_Address_2_NWS',
        csz_col='Address_CSZ_NWS',
        addr1_raw='Principal_Address_1',    addr2_raw='Principal_Address_2',
        city_raw='Principal_City',          state_raw='Principal_State',
        postal_raw='Principal_Postal_Code', country_raw='Principal_Country',
        flag_col='is_PR_Address',           fk_col='Principal_Address_ID',
    ),
    'ra': dict(
        label='Registered Agent',
        nws1_col='RA_Address_1_NWS',        nws2_col='RA_Address_2_NWS',
        csz_col='RA_CSZ_NWS',
        addr1_raw='Registered_Agent_Street_Address_1',
        addr2_raw='Registered_Agent_Street_Address_2',
        city_raw='Registered_Agent_City',   state_raw='Registered_Agent_State',
        postal_raw='Registered_Agent_Postal_Code',
        country_raw='Registered_Agent_Country',
        flag_col='is_RA_Address',           fk_col='Registered_Agent_Address_ID',
    ),
}


def _dim_address_upsert(conn, cfg):
    cur = conn.cursor()

    cur.execute(f"""
        INSERT INTO dbo.DIM_Address
            (Address_NWS, Street_Address_1, Street_Address_2,
             City, [State], Postal_code, Country, {cfg['flag_col']})
        SELECT nws_key,
               MIN({cfg['addr1_raw']}), MIN({cfg['addr2_raw']}),
               MIN({cfg['city_raw']}),  MIN({cfg['state_raw']}),
               MIN({cfg['postal_raw']}), MIN({cfg['country_raw']}), 1
        FROM (
            SELECT ISNULL({cfg['nws1_col']},'') + ISNULL({cfg['nws2_col']},'')
                       + ISNULL({cfg['csz_col']}, '') AS nws_key,
                   {cfg['addr1_raw']}, {cfg['addr2_raw']},
                   {cfg['city_raw']},  {cfg['state_raw']},
                   {cfg['postal_raw']}, {cfg['country_raw']}
            FROM   dbo.Staging_Registration
            WHERE  {cfg['nws1_col']} IS NOT NULL
               OR  {cfg['nws2_col']} IS NOT NULL
               OR  {cfg['csz_col']}  IS NOT NULL
        ) src
        WHERE NOT EXISTS (
            SELECT 1 FROM dbo.DIM_Address d
            WHERE  d.Address_NWS = src.nws_key
        )
        GROUP BY nws_key
    """)
    inserted = cur.rowcount
    conn.commit()

    cur.execute(f"""
        UPDATE d SET d.{cfg['flag_col']} = 1
        FROM   dbo.DIM_Address d
        WHERE  d.{cfg['flag_col']} IS NULL
          AND  EXISTS (
              SELECT 1 FROM dbo.Staging_Registration s
              WHERE  d.Address_NWS = ISNULL(s.{cfg['nws1_col']},'')
                                   + ISNULL(s.{cfg['nws2_col']},'')
                                   + ISNULL(s.{cfg['csz_col']}, '')
          )
    """)
    conn.commit()

    cur.execute(f"""
        UPDATE s SET s.{cfg['fk_col']} = d.ID
        FROM   dbo.Staging_Registration s
        JOIN   dbo.DIM_Address d
            ON d.Address_NWS = ISNULL(s.{cfg['nws1_col']},'')
                             + ISNULL(s.{cfg['nws2_col']},'')
                             + ISNULL(s.{cfg['csz_col']}, '')
        WHERE  s.{cfg['fk_col']} IS NULL
    """)
    fk_updated = cur.rowcount
    conn.commit()

    print(f"  {cfg['label']}: {inserted:,} new DIM_Address rows; {fk_updated:,} FK updates.")


def phase_address(conn):
    print("\n=== Phase 2: DIM_Address upsert ===")
    for cfg in _DIM_ADDR_CFGS.values():
        _dim_address_upsert(conn, cfg)


# ---------------------------------------------------------------------------
# Phase 3 -- Company name normalization + DIM_Company upsert
# ---------------------------------------------------------------------------

def phase_company(conn):
    print("\n=== Phase 3: DIM_Company upsert ===")
    cur = conn.cursor()
    cur.fast_executemany = True

    # Compute Company_Name_Normalized in Python for unstaged rows
    rows = cur.execute("""
        SELECT Staging_ID, Company_Name
        FROM   dbo.Staging_Registration
        WHERE  Company_Name_Normalized IS NULL
          AND  Company_Name IS NOT NULL
    """).fetchall()

    if rows:
        params = [(normalize_company_name(r[1]), r[0]) for r in rows]
        cur.executemany("""
            UPDATE dbo.Staging_Registration
            SET    Company_Name_Normalized = ?
            WHERE  Staging_ID = ?
        """, params)
        conn.commit()
        print(f"  Normalized {len(params):,} company names.")

    # Insert new DIM_Company rows
    cur.execute("""
        INSERT INTO dbo.DIM_Company
            (Original_Formation_Juris_ID, Company_Name, Company_Name_Normalized)
        SELECT s.Original_Formation_Juris_ID,
               MIN(s.Company_Name),
               s.Company_Name_Normalized
        FROM   dbo.Staging_Registration s
        WHERE  s.Original_Formation_Juris_ID IS NOT NULL
          AND  s.Company_Name_Normalized     IS NOT NULL
          AND  NOT EXISTS (
              SELECT 1 FROM dbo.DIM_Company c
              WHERE  c.Original_Formation_Juris_ID = s.Original_Formation_Juris_ID
                AND  c.Company_Name_Normalized     = s.Company_Name_Normalized
          )
        GROUP BY s.Original_Formation_Juris_ID, s.Company_Name_Normalized
    """)
    inserted = cur.rowcount
    conn.commit()

    # Write Company_ID back to staging
    cur.execute("""
        UPDATE s SET s.Company_ID = c.ID
        FROM   dbo.Staging_Registration s
        JOIN   dbo.DIM_Company c
            ON c.Original_Formation_Juris_ID = s.Original_Formation_Juris_ID
           AND c.Company_Name_Normalized     = s.Company_Name_Normalized
        WHERE  s.Company_ID IS NULL
    """)
    fk_updated = cur.rowcount
    conn.commit()

    print(f"  {inserted:,} new DIM_Company rows; {fk_updated:,} Company_ID updates.")


# ---------------------------------------------------------------------------
# Phase 4 -- Fact_Registration upsert
#
# Natural key: (Juris_ID, Juris_ID_Number)
# Status_ID and Entity_Type_ID resolved from lookup tables where available.
# Columns that trigger an UPDATE when they differ:
#   Company_Name, Status_State, Entity_Type_State, Registered_Agent_Name,
#   Principal_Address_ID, Registered_Agent_Address_ID,
#   Original_Formation_Juris_ID, Company_ID
# ---------------------------------------------------------------------------

_FACT_INSERT = """
INSERT INTO dbo.Fact_Registration (
    Juris_ID, Juris_ID_Number, Original_Formation_Juris_ID,
    Company_Name, Company_ID,
    Formation_Date_ID,
    Status_State,      Status_ID,
    Entity_Type_State, Entity_Type_ID,
    Principal_Address_1, Principal_Address_2,
    Principal_City, Principal_State, Principal_Postal_Code, Principal_Country,
    Principal_Address_1_NWS, Principal_Address_2_NWS, Address_CSZ_NWS,
    Principal_Address_ID,
    Registered_Agent_Name,
    Registered_Agent_Street_Address_1, Registered_Agent_Street_Address_2,
    Registered_Agent_City, Registered_Agent_State,
    Registered_Agent_Postal_Code, Registered_Agent_Country,
    RA_Address_1_NWS, RA_Address_2_NWS, RA_CSZ_NWS,
    Registered_Agent_Address_ID,
    Annual_Report_Due_Date, Last_Filed_Date, LLC_Structure,
    Record_Insert_Date, Record_Update_Date
)
SELECT
    s.Juris_ID, s.Juris_ID_Number, s.Original_Formation_Juris_ID,
    s.Company_Name, s.Company_ID,
    s.Formation_Date_ID,
    s.Status_State,      st.ID,
    s.Entity_Type_State, et.ID,
    s.Principal_Address_1, s.Principal_Address_2,
    s.Principal_City, s.Principal_State, s.Principal_Postal_Code, s.Principal_Country,
    s.Principal_Address_1_NWS, s.Principal_Address_2_NWS, s.Address_CSZ_NWS,
    s.Principal_Address_ID,
    s.Registered_Agent_Name,
    s.Registered_Agent_Street_Address_1, s.Registered_Agent_Street_Address_2,
    s.Registered_Agent_City, s.Registered_Agent_State,
    s.Registered_Agent_Postal_Code, s.Registered_Agent_Country,
    s.RA_Address_1_NWS, s.RA_Address_2_NWS, s.RA_CSZ_NWS,
    s.Registered_Agent_Address_ID,
    s.Annual_Report_Due_Date, s.Last_Filed_Date, s.LLC_Structure,
    GETDATE(), GETDATE()
FROM  dbo.Staging_Registration s
LEFT  JOIN dbo.DIM_Status      st ON st.Status_Code      = s.Status_State
LEFT  JOIN dbo.DIM_Entity_Type et ON et.Entity_Type_Code = s.Entity_Type_State
WHERE NOT EXISTS (
    SELECT 1 FROM dbo.Fact_Registration f
    WHERE  f.Juris_ID        = s.Juris_ID
      AND  f.Juris_ID_Number = s.Juris_ID_Number
);
"""

_FACT_UPDATE = """
UPDATE f
SET
    f.Company_Name                      = s.Company_Name,
    f.Company_ID                        = s.Company_ID,
    f.Original_Formation_Juris_ID       = s.Original_Formation_Juris_ID,
    f.Status_State                      = s.Status_State,
    f.Status_ID                         = ISNULL(st.ID, f.Status_ID),
    f.Entity_Type_State                 = s.Entity_Type_State,
    f.Entity_Type_ID                    = ISNULL(et.ID, f.Entity_Type_ID),
    f.Registered_Agent_Name             = s.Registered_Agent_Name,
    f.Principal_Address_1               = s.Principal_Address_1,
    f.Principal_Address_2               = s.Principal_Address_2,
    f.Principal_City                    = s.Principal_City,
    f.Principal_State                   = s.Principal_State,
    f.Principal_Postal_Code             = s.Principal_Postal_Code,
    f.Principal_Country                 = s.Principal_Country,
    f.Principal_Address_1_NWS           = s.Principal_Address_1_NWS,
    f.Principal_Address_2_NWS           = s.Principal_Address_2_NWS,
    f.Address_CSZ_NWS                   = s.Address_CSZ_NWS,
    f.Principal_Address_ID              = s.Principal_Address_ID,
    f.Registered_Agent_Street_Address_1 = s.Registered_Agent_Street_Address_1,
    f.Registered_Agent_Street_Address_2 = s.Registered_Agent_Street_Address_2,
    f.Registered_Agent_City             = s.Registered_Agent_City,
    f.Registered_Agent_State            = s.Registered_Agent_State,
    f.Registered_Agent_Postal_Code      = s.Registered_Agent_Postal_Code,
    f.Registered_Agent_Country          = s.Registered_Agent_Country,
    f.RA_Address_1_NWS                  = s.RA_Address_1_NWS,
    f.RA_Address_2_NWS                  = s.RA_Address_2_NWS,
    f.RA_CSZ_NWS                        = s.RA_CSZ_NWS,
    f.Registered_Agent_Address_ID       = s.Registered_Agent_Address_ID,
    f.Annual_Report_Due_Date            = s.Annual_Report_Due_Date,
    f.Last_Filed_Date                   = s.Last_Filed_Date,
    f.LLC_Structure                     = s.LLC_Structure,
    f.Record_Update_Date                = GETDATE()
FROM  dbo.Fact_Registration f
JOIN  dbo.Staging_Registration s
    ON  s.Juris_ID        = f.Juris_ID
    AND s.Juris_ID_Number = f.Juris_ID_Number
LEFT  JOIN dbo.DIM_Status      st ON st.Status_Code      = s.Status_State
LEFT  JOIN dbo.DIM_Entity_Type et ON et.Entity_Type_Code = s.Entity_Type_State
WHERE
    ISNULL(f.Company_Name,'')                    != ISNULL(s.Company_Name,'')
    OR ISNULL(f.Company_ID,-1)                   != ISNULL(s.Company_ID,-1)
    OR ISNULL(f.Status_State,'')                 != ISNULL(s.Status_State,'')
    OR ISNULL(f.Entity_Type_State,'')            != ISNULL(s.Entity_Type_State,'')
    OR ISNULL(f.Registered_Agent_Name,'')        != ISNULL(s.Registered_Agent_Name,'')
    OR ISNULL(f.Principal_Address_ID,-1)         != ISNULL(s.Principal_Address_ID,-1)
    OR ISNULL(f.Registered_Agent_Address_ID,-1)  != ISNULL(s.Registered_Agent_Address_ID,-1)
    OR ISNULL(f.Original_Formation_Juris_ID,-1)  != ISNULL(s.Original_Formation_Juris_ID,-1);
"""


def phase_fact(conn):
    print("\n=== Phase 4: Fact_Registration upsert ===")
    cur = conn.cursor()
    t0  = time.time()

    cur.execute(_FACT_INSERT)
    inserted = cur.rowcount
    conn.commit()

    cur.execute(_FACT_UPDATE)
    updated = cur.rowcount
    conn.commit()

    print(f"  Inserted: {inserted:,}  Updated: {updated:,}  ({time.time()-t0:.1f}s)")


# ---------------------------------------------------------------------------
# Phase 5 -- DIM_Registration Type 2 SCD
# ---------------------------------------------------------------------------

_DIM_REG_CLOSE = """
UPDATE dr
SET    dr.Valid_To   = CAST(GETDATE() AS DATE),
       dr.Is_Current = 0
FROM   dbo.DIM_Registration dr
JOIN   dbo.Fact_Registration f  ON f.ID = dr.Registration_ID
JOIN   dbo.Staging_Registration s
    ON  s.Juris_ID        = f.Juris_ID
    AND s.Juris_ID_Number = f.Juris_ID_Number
WHERE  dr.Is_Current = 1
  AND (
       ISNULL(dr.Company_Name,'')                    != ISNULL(s.Company_Name,'')
    OR ISNULL(dr.Status_State,'')                   != ISNULL(s.Status_State,'')
    OR ISNULL(dr.Entity_Type_State,'')              != ISNULL(s.Entity_Type_State,'')
    OR ISNULL(dr.Registered_Agent_Name,'')          != ISNULL(s.Registered_Agent_Name,'')
    OR ISNULL(dr.Principal_Address_ID,-1)           != ISNULL(s.Principal_Address_ID,-1)
    OR ISNULL(dr.Registered_Agent_Address_ID,-1)    != ISNULL(s.Registered_Agent_Address_ID,-1)
  );
"""

_DIM_REG_INSERT = """
INSERT INTO dbo.DIM_Registration
    (Registration_ID, Company_Name, Status_State, Entity_Type_State,
     Registered_Agent_Name, Principal_Address_ID, Registered_Agent_Address_ID,
     Valid_From, Valid_To, Is_Current)
SELECT
    f.ID,
    s.Company_Name, s.Status_State, s.Entity_Type_State,
    s.Registered_Agent_Name,
    s.Principal_Address_ID, s.Registered_Agent_Address_ID,
    CAST(GETDATE() AS DATE), NULL, 1
FROM  dbo.Fact_Registration f
JOIN  dbo.Staging_Registration s
    ON  s.Juris_ID        = f.Juris_ID
    AND s.Juris_ID_Number = f.Juris_ID_Number
WHERE NOT EXISTS (
    SELECT 1 FROM dbo.DIM_Registration dr
    WHERE  dr.Registration_ID = f.ID
      AND  dr.Is_Current      = 1
);
"""


def phase_dim_reg(conn):
    print("\n=== Phase 5: DIM_Registration Type 2 SCD ===")
    cur = conn.cursor()
    t0  = time.time()

    cur.execute(_DIM_REG_CLOSE)
    closed = cur.rowcount
    conn.commit()

    cur.execute(_DIM_REG_INSERT)
    inserted = cur.rowcount
    conn.commit()

    print(f"  Closed: {closed:,}  Inserted: {inserted:,}  ({time.time()-t0:.1f}s)")


# ---------------------------------------------------------------------------
# Phase 6 -- Fact_Registration_Address Type 2 SCD
# ---------------------------------------------------------------------------

_FRA_CLOSE = """
UPDATE fra
SET    fra.Valid_To   = CAST(GETDATE() AS DATE),
       fra.Is_Current = 0
FROM   dbo.Fact_Registration_Address fra
JOIN   dbo.Fact_Registration f ON f.ID = fra.Registration_ID
JOIN   dbo.Staging_Registration s
    ON  s.Juris_ID        = f.Juris_ID
    AND s.Juris_ID_Number = f.Juris_ID_Number
WHERE  fra.Is_Current = 1
  AND (
       (fra.Address_Type = 'Principal'
        AND ISNULL(fra.Address_ID,-1) != ISNULL(s.Principal_Address_ID,-1))
    OR (fra.Address_Type = 'Registered Agent'
        AND ISNULL(fra.Address_ID,-1) != ISNULL(s.Registered_Agent_Address_ID,-1))
  );
"""

_FRA_INSERT = """
INSERT INTO dbo.Fact_Registration_Address
    (Registration_ID, Address_ID, Address_Type, Valid_From, Valid_To, Is_Current)
SELECT f.ID, s.Principal_Address_ID, 'Principal',
       CAST(GETDATE() AS DATE), NULL, 1
FROM   dbo.Fact_Registration f
JOIN   dbo.Staging_Registration s
    ON  s.Juris_ID        = f.Juris_ID
    AND s.Juris_ID_Number = f.Juris_ID_Number
WHERE  s.Principal_Address_ID IS NOT NULL
  AND  NOT EXISTS (
      SELECT 1 FROM dbo.Fact_Registration_Address fra
      WHERE  fra.Registration_ID = f.ID
        AND  fra.Address_Type    = 'Principal'
        AND  fra.Is_Current      = 1
  )
UNION ALL
SELECT f.ID, s.Registered_Agent_Address_ID, 'Registered Agent',
       CAST(GETDATE() AS DATE), NULL, 1
FROM   dbo.Fact_Registration f
JOIN   dbo.Staging_Registration s
    ON  s.Juris_ID        = f.Juris_ID
    AND s.Juris_ID_Number = f.Juris_ID_Number
WHERE  s.Registered_Agent_Address_ID IS NOT NULL
  AND  NOT EXISTS (
      SELECT 1 FROM dbo.Fact_Registration_Address fra
      WHERE  fra.Registration_ID = f.ID
        AND  fra.Address_Type    = 'Registered Agent'
        AND  fra.Is_Current      = 1
  );
"""


def phase_fra(conn):
    print("\n=== Phase 6: Fact_Registration_Address Type 2 SCD ===")
    cur = conn.cursor()
    t0  = time.time()

    cur.execute(_FRA_CLOSE)
    closed = cur.rowcount
    conn.commit()

    cur.execute(_FRA_INSERT)
    inserted = cur.rowcount
    conn.commit()

    print(f"  Closed: {closed:,}  Inserted: {inserted:,}  ({time.time()-t0:.1f}s)")


# ---------------------------------------------------------------------------
# Phase 7 -- DIM_Registration_Principal Type 2 SCD  (individual-level)
#
# Natural key:  (Registration_ID, First_Name, Middle_Name, Last_Name, Name_Suffix)
# Tracked cols: Title, Name_Prefix
#
# Three sub-steps:
#   a. Close rows where tracked columns changed for a known person.
#   b. Close rows for principals no longer present for their registration.
#   c. Insert new rows for new/changed/reactivated principals.
#
# Requires:  dbo.Staging_Registration_Principal
#   Columns: Juris_ID, Juris_ID_Number, Title, Name_Prefix,
#            First_Name, Middle_Name, Last_Name, Name_Suffix
# ---------------------------------------------------------------------------

_PRINCIPAL_TABLE_CHECK = """
SELECT OBJECT_ID(N'dbo.Staging_Registration_Principal', N'U')
"""

_PRINCIPAL_CLOSE_CHANGED = """
UPDATE drp
SET    drp.Valid_To   = CAST(GETDATE() AS DATE),
       drp.Is_Current = 0
FROM   dbo.DIM_Registration_Principal drp
JOIN   dbo.Fact_Registration f ON f.ID = drp.Registration_ID
JOIN   dbo.Staging_Registration_Principal sp
    ON  sp.Juris_ID        = f.Juris_ID
    AND sp.Juris_ID_Number = f.Juris_ID_Number
    AND ISNULL(sp.First_Name,'')  = ISNULL(drp.First_Name,'')
    AND ISNULL(sp.Middle_Name,'') = ISNULL(drp.Middle_Name,'')
    AND sp.Last_Name              = drp.Last_Name
    AND ISNULL(sp.Name_Suffix,'') = ISNULL(drp.Name_Suffix,'')
WHERE  drp.Is_Current = 1
  AND (ISNULL(drp.Title,'')       != ISNULL(sp.Title,'')
    OR ISNULL(drp.Name_Prefix,'') != ISNULL(sp.Name_Prefix,''));
"""

_PRINCIPAL_CLOSE_REMOVED = """
UPDATE drp
SET    drp.Valid_To   = CAST(GETDATE() AS DATE),
       drp.Is_Current = 0
FROM   dbo.DIM_Registration_Principal drp
JOIN   dbo.Fact_Registration f ON f.ID = drp.Registration_ID
WHERE  drp.Is_Current = 1
  AND  EXISTS (
      SELECT 1 FROM dbo.Staging_Registration sr
      WHERE  sr.Juris_ID        = f.Juris_ID
        AND  sr.Juris_ID_Number = f.Juris_ID_Number
  )
  AND  NOT EXISTS (
      SELECT 1 FROM dbo.Staging_Registration_Principal sp
      WHERE  sp.Juris_ID             = f.Juris_ID
        AND  sp.Juris_ID_Number      = f.Juris_ID_Number
        AND  ISNULL(sp.First_Name,'')  = ISNULL(drp.First_Name,'')
        AND  ISNULL(sp.Middle_Name,'') = ISNULL(drp.Middle_Name,'')
        AND  sp.Last_Name              = drp.Last_Name
        AND  ISNULL(sp.Name_Suffix,'') = ISNULL(drp.Name_Suffix,'')
  );
"""

_PRINCIPAL_INSERT = """
INSERT INTO dbo.DIM_Registration_Principal
    (Registration_ID, Title, Name_Prefix,
     First_Name, Middle_Name, Last_Name, Name_Suffix,
     Valid_From, Valid_To, Is_Current)
SELECT
    f.ID,
    sp.Title, sp.Name_Prefix,
    sp.First_Name, sp.Middle_Name, sp.Last_Name, sp.Name_Suffix,
    CAST(GETDATE() AS DATE), NULL, 1
FROM  dbo.Staging_Registration_Principal sp
JOIN  dbo.Fact_Registration f
    ON  f.Juris_ID        = sp.Juris_ID
    AND f.Juris_ID_Number = sp.Juris_ID_Number
WHERE NOT EXISTS (
    SELECT 1 FROM dbo.DIM_Registration_Principal drp
    WHERE  drp.Registration_ID       = f.ID
      AND  ISNULL(drp.First_Name,'')  = ISNULL(sp.First_Name,'')
      AND  ISNULL(drp.Middle_Name,'') = ISNULL(sp.Middle_Name,'')
      AND  drp.Last_Name              = sp.Last_Name
      AND  ISNULL(drp.Name_Suffix,'') = ISNULL(sp.Name_Suffix,'')
      AND  drp.Is_Current             = 1
);
"""


def phase_principal(conn):
    print("\n=== Phase 7: DIM_Registration_Principal Type 2 SCD ===")
    cur = conn.cursor()

    cur.execute(_PRINCIPAL_TABLE_CHECK)
    if cur.fetchone()[0] is None:
        print("  Staging_Registration_Principal does not exist -- skipping.")
        return

    t0 = time.time()

    cur.execute(_PRINCIPAL_CLOSE_CHANGED)
    changed = cur.rowcount
    conn.commit()

    cur.execute(_PRINCIPAL_CLOSE_REMOVED)
    removed = cur.rowcount
    conn.commit()

    cur.execute(_PRINCIPAL_INSERT)
    inserted = cur.rowcount
    conn.commit()

    print(f"  Closed-changed: {changed:,}  Closed-removed: {removed:,}  "
          f"Inserted: {inserted:,}  ({time.time()-t0:.1f}s)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

PHASES = {
    'nws':       phase_nws,
    'address':   phase_address,
    'company':   phase_company,
    'fact':      phase_fact,
    'dim_reg':   phase_dim_reg,
    'fra':       phase_fra,
    'principal': phase_principal,
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        '--phase',
        choices=['all', 'nws', 'address', 'company', 'fact', 'dim_reg', 'fra', 'principal'],
        default='all',
    )
    ap.add_argument('--batch-size', type=int, default=100_000)
    ap.add_argument('--dry-run', action='store_true',
                    help='Roll back all changes after running -- for testing.')
    args = ap.parse_args()

    conn = pyodbc.connect(CONN_STR)
    conn.autocommit = False

    if args.dry_run:
        print("DRY RUN -- changes will be rolled back.")

    phases_to_run = list(PHASES.keys()) if args.phase == 'all' else [args.phase]

    for name in phases_to_run:
        fn = PHASES[name]
        if name == 'nws':
            fn(conn, args.batch_size)
        else:
            fn(conn)

    if args.dry_run:
        conn.rollback()
        print("\nDry run complete -- all changes rolled back.")
    else:
        conn.close()
        print("\nFinished.")


if __name__ == '__main__':
    main()
