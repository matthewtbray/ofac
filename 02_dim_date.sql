-- ============================================================
-- 02_dim_date.sql
-- Creates and populates DIM_Date from 1700-01-01 to 2075-12-31.
-- DateKey format: YYYYMMDD (INT)
-- Run in: Registrations_DW
-- ============================================================

IF OBJECT_ID(N'dbo.DIM_Date', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.DIM_Date (
        DateKey         INT          NOT NULL PRIMARY KEY,  -- YYYYMMDD
        [Date]          DATE         NOT NULL,
        [Year]          SMALLINT     NOT NULL,
        [Month]         TINYINT      NOT NULL,
        [Day]           TINYINT      NOT NULL,
        Quarter         TINYINT      NOT NULL,
        Month_Name      VARCHAR(10)  NOT NULL,
        Day_Name        VARCHAR(10)  NOT NULL,
        Day_Of_Year     SMALLINT     NOT NULL,
        Week_Of_Year    TINYINT      NOT NULL,
        Is_Weekend      BIT          NOT NULL,
        Is_Leap_Year    BIT          NOT NULL,
        YYYYMM          CHAR(6)      NOT NULL,
        First_Of_Month  DATE         NOT NULL,
        Last_Of_Month   DATE         NOT NULL,
        First_Of_Quarter DATE        NOT NULL,
        First_Of_Year   DATE         NOT NULL
    );
END;
GO

-- Populate via recursive CTE (MAXRECURSION 0 removes the 100-level default limit).
-- Inserts only dates not already present so it is safe to re-run.

WITH dates AS (
    SELECT CAST('1700-01-01' AS DATE) AS d
    UNION ALL
    SELECT DATEADD(DAY, 1, d)
    FROM   dates
    WHERE  d < '2075-12-31'
)
INSERT INTO dbo.DIM_Date (
    DateKey, [Date], [Year], [Month], [Day],
    Quarter, Month_Name, Day_Name, Day_Of_Year, Week_Of_Year,
    Is_Weekend, Is_Leap_Year, YYYYMM,
    First_Of_Month, Last_Of_Month, First_Of_Quarter, First_Of_Year
)
SELECT
    CAST(FORMAT(d, 'yyyyMMdd') AS INT),
    d,
    YEAR(d),
    MONTH(d),
    DAY(d),
    DATEPART(QUARTER, d),
    DATENAME(MONTH, d),
    DATENAME(WEEKDAY, d),
    DATEPART(DAYOFYEAR, d),
    DATEPART(WEEK, d),
    CASE WHEN DATEPART(WEEKDAY, d) IN (1, 7) THEN 1 ELSE 0 END,
    CASE WHEN YEAR(d) % 400 = 0
              OR (YEAR(d) % 4 = 0 AND YEAR(d) % 100 != 0) THEN 1 ELSE 0 END,
    FORMAT(d, 'yyyyMM'),
    DATEFROMPARTS(YEAR(d), MONTH(d), 1),
    EOMONTH(d),
    DATEFROMPARTS(YEAR(d), (DATEPART(QUARTER, d) - 1) * 3 + 1, 1),
    DATEFROMPARTS(YEAR(d), 1, 1)
FROM  dates
WHERE NOT EXISTS (
    SELECT 1 FROM dbo.DIM_Date x
    WHERE  x.DateKey = CAST(FORMAT(d, 'yyyyMMdd') AS INT)
)
OPTION (MAXRECURSION 0);
GO
