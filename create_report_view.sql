-- ============================================================
-- MatchingResults_Report  (VIEW)
-- Consolidates all positive match records from every
-- MatchingResults_* name/AKA table into a single reporting surface.
-- Run this script once against SDNReporting.
-- Filter by Run_ID to scope to a specific matching run.
--
-- Address matching is not surfaced as standalone rows.
-- Instead, city/country context is joined from MatchingResults_Address
-- onto each name/AKA row via Input_Record_ID + SDN_UID.
-- The five Country_City_* columns are NULL when no qualifying
-- address match (City_JW >= 70, Country_JW = 0 or >= 95) exists.
-- ============================================================

CREATE OR ALTER VIEW [dbo].[MatchingResults_Report] AS

WITH BestAddr AS (
    -- Best-scoring address match per (Input_Record_ID, SDN entry)
    -- that clears the city >= 70% and country thresholds.
    SELECT
        Input_Record_ID,
        SDNEntry_UID,
        City_JaroWinklerSimilarity  AS City_JW,
        SourceCity,
        SDNCity,
        SourceCountry,
        SDNCountry
    FROM (
        SELECT
            Input_Record_ID,
            SDNEntry_UID,
            City_JaroWinklerSimilarity,
            SourceCity,
            SDNCity,
            SourceCountry,
            SDNCountry,
            ROW_NUMBER() OVER (
                PARTITION BY Input_Record_ID, SDNEntry_UID
                ORDER BY City_JaroWinklerSimilarity DESC
            ) AS rn
        FROM   dbo.MatchingResults_Address
        WHERE  City_JaroWinklerSimilarity > 70
        AND    Country_JaroWinklerSimilarity > 70
    ) x
    WHERE rn = 1
)

-- ---- 1. Person primary-name matches ----------------------------------
SELECT
    p.Run_ID,
    p.Input_Record_ID,
    p.SDN_UID,
    p.SDN_Publish_Date,
    'Person_Name'                                              AS Match_Type,
    LTRIM(RTRIM(
        COALESCE(p.SourceFN + ' ', '')
      + COALESCE(p.SourceMN + ' ', '')
      + COALESCE(p.SourceLN,       '')))                      AS Input_Name,
    LTRIM(RTRIM(
        COALESCE(p.SDNFN + ' ', '')
      + COALESCE(p.SDNLN,      '')))                          AS SDN_Name,
    p.FirstName_JaroWinklerSimilarity                          AS FirstName_JW,
    p.LastName_JaroWinklerSimilarity                           AS LastName_JW,
    NULL                                                       AS FullName_JW,
    CAST(NULL AS INT)                                          AS AKA_UID,
    CAST(NULL AS VARCHAR(50))                                  AS AKA_Category,
    CAST(NULL AS VARCHAR(500))                                 AS LinkedTo_Text,
    CAST(NULL AS VARCHAR(100))                                 AS Input_Phone,
    CAST(NULL AS VARCHAR(100))                                 AS SDN_Phone,
    p.SDN_Type,
    ba.City_JW                                                 AS Country_City_Match,
    ba.SourceCity                                              AS Input_City,
    ba.SDNCity                                                 AS SDN_City,
    ba.SourceCountry                                           AS Input_Country,
    ba.SDNCountry                                              AS SDN_Country
FROM   dbo.MatchingResults_Person_Full p
LEFT JOIN BestAddr ba
    ON  ba.Input_Record_ID = p.Input_Record_ID
    AND ba.SDNEntry_UID    = p.SDN_UID
WHERE  p.Personal_Name_Match = 1

UNION ALL

-- ---- 2. Person AKA matches -------------------------------------------
SELECT
    a.Run_ID,
    a.Input_Record_ID,
    a.SDN_UID,
    NULL,
    'Person_AKA',
    LTRIM(RTRIM(
        COALESCE(a.SourceFN + ' ', '')
      + COALESCE(a.SourceMN + ' ', '')
      + COALESCE(a.SourceLN,       ''))),
    LTRIM(RTRIM(
        COALESCE(a.SDNFN + ' ', '')
      + COALESCE(a.SDNLN,      ''))),
    a.FirstName_JaroWinklerSimilarity,
    a.LastName_JaroWinklerSimilarity,
    NULL,
    a.AKA_UID,
    a.AKA_Category,
    NULL,
    NULL,
    NULL,
    CAST(NULL AS VARCHAR(50)),
    ba.City_JW,
    ba.SourceCity,
    ba.SDNCity,
    ba.SourceCountry,
    ba.SDNCountry
FROM   dbo.MatchingResults_AKA a
LEFT JOIN BestAddr ba
    ON  ba.Input_Record_ID = a.Input_Record_ID
    AND ba.SDNEntry_UID    = a.SDN_UID
WHERE  a.Personal_Name_Match = 1

UNION ALL

-- ---- 3. Org / entity primary-name matches ----------------------------
SELECT
    o.Run_ID,
    o.Input_Record_ID,
    o.SDN_UID,
    o.SDN_Publish_Date,
    'Org_Name',
    o.SourceOrgName,
    o.SDNOrgName,
    NULL,
    NULL,
    o.FullName_JaroWinklerSimilarity,
    NULL,
    NULL,
    NULL,
    NULL,
    NULL,
    o.SDN_Type,
    ba.City_JW,
    ba.SourceCity,
    ba.SDNCity,
    ba.SourceCountry,
    ba.SDNCountry
FROM   dbo.MatchingResults_OrgName o
LEFT JOIN BestAddr ba
    ON  ba.Input_Record_ID = o.Input_Record_ID
    AND ba.SDNEntry_UID    = o.SDN_UID
WHERE  o.FullName_JaroWinklerSimilarity >= 85

UNION ALL

-- ---- 4. Org / entity AKA matches ------------------------------------
SELECT
    oa.Run_ID,
    oa.Input_Record_ID,
    oa.SDN_UID,
    oa.SDN_Publish_Date,
    'Org_AKA',
    oa.SourceOrgName,
    oa.SDNOrgName,
    NULL,
    NULL,
    oa.FullName_JaroWinklerSimilarity,
    oa.AKA_UID,
    oa.AKA_Category,
    NULL,
    NULL,
    NULL,
    oa.SDN_Type,
    ba.City_JW,
    ba.SourceCity,
    ba.SDNCity,
    ba.SourceCountry,
    ba.SDNCountry
FROM   dbo.MatchingResults_OrgName_AKA oa
LEFT JOIN BestAddr ba
    ON  ba.Input_Record_ID = oa.Input_Record_ID
    AND ba.SDNEntry_UID    = oa.SDN_UID
WHERE  oa.FullName_JaroWinklerSimilarity >= 85

UNION ALL

-- ---- 5. Linked-to matches -------------------------------------------
SELECT
    lt.Run_ID,
    lt.Input_Record_ID,
    lt.SDN_UID,
    lt.SDN_Publish_Date,
    'LinkedTo',
    lt.SourceName,
    lt.LinkedTo_Text,
    NULL,
    NULL,
    lt.FullName_JaroWinklerSimilarity,
    NULL,
    NULL,
    lt.LinkedTo_Text,
    NULL,
    NULL,
    NULL,
    ba.City_JW,
    ba.SourceCity,
    ba.SDNCity,
    ba.SourceCountry,
    ba.SDNCountry
FROM   dbo.MatchingResults_LinkedTo lt
LEFT JOIN BestAddr ba
    ON  ba.Input_Record_ID = lt.Input_Record_ID
    AND ba.SDNEntry_UID    = lt.SDN_UID
WHERE  lt.FullName_JaroWinklerSimilarity >= 85

UNION ALL

-- ---- 6. Phone matches -----------------------------------------------
SELECT
    ph.Run_ID,
    ph.Input_Record_ID,
    ph.SDN_UID,
    ph.SDN_Publish_Date,
    'Phone',
    ph.Input_Phone_Raw,
    ph.SDN_Phone_Raw,
    NULL,
    NULL,
    ph.JaroWinkler_Digits,
    NULL,
    NULL,
    NULL,
    ph.Input_Phone_Raw,
    ph.SDN_Phone_Raw,
    NULL,
    ba.City_JW,
    ba.SourceCity,
    ba.SDNCity,
    ba.SourceCountry,
    ba.SDNCountry
FROM   dbo.MatchingResults_Phone ph
LEFT JOIN BestAddr ba
    ON  ba.Input_Record_ID = ph.Input_Record_ID
    AND ba.SDNEntry_UID    = ph.SDN_UID;
