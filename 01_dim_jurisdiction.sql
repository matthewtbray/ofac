-- ============================================================
-- 01_dim_jurisdiction.sql
-- Creates and populates DIM_Jurisdiction.
-- Covers: US states/territories, Canadian provinces/territories,
--         and ~195 countries.
-- Self-referencing parent/child: Country rows have Parent_ID = NULL;
-- state/province rows have Parent_ID = their country row.
-- Run in: Registrations_DW
-- ============================================================

-- ------------------------------------------------------------
-- DDL
-- ------------------------------------------------------------

IF OBJECT_ID(N'dbo.DIM_Jurisdiction', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.DIM_Jurisdiction (
        ID              INT          NOT NULL IDENTITY(1,1) PRIMARY KEY,
        Jurisdiction    VARCHAR(100) NOT NULL,
        Abbreviation    VARCHAR(10)  NULL,
        Juris_Type      VARCHAR(20)  NOT NULL,   -- 'Country', 'State', 'Province', 'Territory'
        Parent_ID       INT          NULL,
        ISO2            CHAR(2)      NULL,
        FIPS            CHAR(2)      NULL,

        CONSTRAINT FK_Juris_Parent FOREIGN KEY (Parent_ID)
            REFERENCES dbo.DIM_Jurisdiction (ID)
    );
    CREATE INDEX IX_Juris_Parent ON dbo.DIM_Jurisdiction (Parent_ID);
END;
GO

-- ------------------------------------------------------------
-- Countries
-- ------------------------------------------------------------

INSERT INTO dbo.DIM_Jurisdiction (Jurisdiction, Abbreviation, Juris_Type, Parent_ID, ISO2)
SELECT Jurisdiction, Abbreviation, 'Country', NULL, ISO2
FROM (VALUES
    ('Afghanistan',                         'AF',  'AF'),
    ('Albania',                             'AL',  'AL'),
    ('Algeria',                             'DZ',  'DZ'),
    ('Andorra',                             'AD',  'AD'),
    ('Angola',                              'AO',  'AO'),
    ('Antigua and Barbuda',                 'AG',  'AG'),
    ('Argentina',                           'AR',  'AR'),
    ('Armenia',                             'AM',  'AM'),
    ('Australia',                           'AU',  'AU'),
    ('Austria',                             'AT',  'AT'),
    ('Azerbaijan',                          'AZ',  'AZ'),
    ('Bahamas',                             'BS',  'BS'),
    ('Bahrain',                             'BH',  'BH'),
    ('Bangladesh',                          'BD',  'BD'),
    ('Barbados',                            'BB',  'BB'),
    ('Belarus',                             'BY',  'BY'),
    ('Belgium',                             'BE',  'BE'),
    ('Belize',                              'BZ',  'BZ'),
    ('Benin',                               'BJ',  'BJ'),
    ('Bhutan',                              'BT',  'BT'),
    ('Bolivia',                             'BO',  'BO'),
    ('Bosnia and Herzegovina',              'BA',  'BA'),
    ('Botswana',                            'BW',  'BW'),
    ('Brazil',                              'BR',  'BR'),
    ('Brunei',                              'BN',  'BN'),
    ('Bulgaria',                            'BG',  'BG'),
    ('Burkina Faso',                        'BF',  'BF'),
    ('Burundi',                             'BI',  'BI'),
    ('Cabo Verde',                          'CV',  'CV'),
    ('Cambodia',                            'KH',  'KH'),
    ('Cameroon',                            'CM',  'CM'),
    ('Canada',                              'CA',  'CA'),
    ('Central African Republic',            'CF',  'CF'),
    ('Chad',                                'TD',  'TD'),
    ('Chile',                               'CL',  'CL'),
    ('China',                               'CN',  'CN'),
    ('Colombia',                            'CO',  'CO'),
    ('Comoros',                             'KM',  'KM'),
    ('Congo (Brazzaville)',                 'CG',  'CG'),
    ('Congo (Kinshasa)',                    'CD',  'CD'),
    ('Costa Rica',                          'CR',  'CR'),
    ('Croatia',                             'HR',  'HR'),
    ('Cuba',                                'CU',  'CU'),
    ('Cyprus',                              'CY',  'CY'),
    ('Czech Republic',                      'CZ',  'CZ'),
    ('Denmark',                             'DK',  'DK'),
    ('Djibouti',                            'DJ',  'DJ'),
    ('Dominica',                            'DM',  'DM'),
    ('Dominican Republic',                  'DO',  'DO'),
    ('Ecuador',                             'EC',  'EC'),
    ('Egypt',                               'EG',  'EG'),
    ('El Salvador',                         'SV',  'SV'),
    ('Equatorial Guinea',                   'GQ',  'GQ'),
    ('Eritrea',                             'ER',  'ER'),
    ('Estonia',                             'EE',  'EE'),
    ('Eswatini',                            'SZ',  'SZ'),
    ('Ethiopia',                            'ET',  'ET'),
    ('Fiji',                                'FJ',  'FJ'),
    ('Finland',                             'FI',  'FI'),
    ('France',                              'FR',  'FR'),
    ('Gabon',                               'GA',  'GA'),
    ('Gambia',                              'GM',  'GM'),
    ('Georgia',                             'GE',  'GE'),
    ('Germany',                             'DE',  'DE'),
    ('Ghana',                               'GH',  'GH'),
    ('Greece',                              'GR',  'GR'),
    ('Grenada',                             'GD',  'GD'),
    ('Guatemala',                           'GT',  'GT'),
    ('Guinea',                              'GN',  'GN'),
    ('Guinea-Bissau',                       'GW',  'GW'),
    ('Guyana',                              'GY',  'GY'),
    ('Haiti',                               'HT',  'HT'),
    ('Honduras',                            'HN',  'HN'),
    ('Hungary',                             'HU',  'HU'),
    ('Iceland',                             'IS',  'IS'),
    ('India',                               'IN',  'IN'),
    ('Indonesia',                           'ID',  'ID'),
    ('Iran',                                'IR',  'IR'),
    ('Iraq',                                'IQ',  'IQ'),
    ('Ireland',                             'IE',  'IE'),
    ('Israel',                              'IL',  'IL'),
    ('Italy',                               'IT',  'IT'),
    ('Jamaica',                             'JM',  'JM'),
    ('Japan',                               'JP',  'JP'),
    ('Jordan',                              'JO',  'JO'),
    ('Kazakhstan',                          'KZ',  'KZ'),
    ('Kenya',                               'KE',  'KE'),
    ('Kiribati',                            'KI',  'KI'),
    ('Korea, North',                        'KP',  'KP'),
    ('Korea, South',                        'KR',  'KR'),
    ('Kosovo',                              'XK',  'XK'),
    ('Kuwait',                              'KW',  'KW'),
    ('Kyrgyzstan',                          'KG',  'KG'),
    ('Laos',                                'LA',  'LA'),
    ('Latvia',                              'LV',  'LV'),
    ('Lebanon',                             'LB',  'LB'),
    ('Lesotho',                             'LS',  'LS'),
    ('Liberia',                             'LR',  'LR'),
    ('Libya',                               'LY',  'LY'),
    ('Liechtenstein',                       'LI',  'LI'),
    ('Lithuania',                           'LT',  'LT'),
    ('Luxembourg',                          'LU',  'LU'),
    ('Madagascar',                          'MG',  'MG'),
    ('Malawi',                              'MW',  'MW'),
    ('Malaysia',                            'MY',  'MY'),
    ('Maldives',                            'MV',  'MV'),
    ('Mali',                                'ML',  'ML'),
    ('Malta',                               'MT',  'MT'),
    ('Marshall Islands',                    'MH',  'MH'),
    ('Mauritania',                          'MR',  'MR'),
    ('Mauritius',                           'MU',  'MU'),
    ('Mexico',                              'MX',  'MX'),
    ('Micronesia',                          'FM',  'FM'),
    ('Moldova',                             'MD',  'MD'),
    ('Monaco',                              'MC',  'MC'),
    ('Mongolia',                            'MN',  'MN'),
    ('Montenegro',                          'ME',  'ME'),
    ('Morocco',                             'MA',  'MA'),
    ('Mozambique',                          'MZ',  'MZ'),
    ('Myanmar',                             'MM',  'MM'),
    ('Namibia',                             'NA',  'NA'),
    ('Nauru',                               'NR',  'NR'),
    ('Nepal',                               'NP',  'NP'),
    ('Netherlands',                         'NL',  'NL'),
    ('New Zealand',                         'NZ',  'NZ'),
    ('Nicaragua',                           'NI',  'NI'),
    ('Niger',                               'NE',  'NE'),
    ('Nigeria',                             'NG',  'NG'),
    ('North Macedonia',                     'MK',  'MK'),
    ('Norway',                              'NO',  'NO'),
    ('Oman',                                'OM',  'OM'),
    ('Pakistan',                            'PK',  'PK'),
    ('Palau',                               'PW',  'PW'),
    ('Palestine',                           'PS',  'PS'),
    ('Panama',                              'PA',  'PA'),
    ('Papua New Guinea',                    'PG',  'PG'),
    ('Paraguay',                            'PY',  'PY'),
    ('Peru',                                'PE',  'PE'),
    ('Philippines',                         'PH',  'PH'),
    ('Poland',                              'PL',  'PL'),
    ('Portugal',                            'PT',  'PT'),
    ('Qatar',                               'QA',  'QA'),
    ('Romania',                             'RO',  'RO'),
    ('Russia',                              'RU',  'RU'),
    ('Rwanda',                              'RW',  'RW'),
    ('Saint Kitts and Nevis',               'KN',  'KN'),
    ('Saint Lucia',                         'LC',  'LC'),
    ('Saint Vincent and the Grenadines',    'VC',  'VC'),
    ('Samoa',                               'WS',  'WS'),
    ('San Marino',                          'SM',  'SM'),
    ('Sao Tome and Principe',               'ST',  'ST'),
    ('Saudi Arabia',                        'SA',  'SA'),
    ('Senegal',                             'SN',  'SN'),
    ('Serbia',                              'RS',  'RS'),
    ('Seychelles',                          'SC',  'SC'),
    ('Sierra Leone',                        'SL',  'SL'),
    ('Singapore',                           'SG',  'SG'),
    ('Slovakia',                            'SK',  'SK'),
    ('Slovenia',                            'SI',  'SI'),
    ('Solomon Islands',                     'SB',  'SB'),
    ('Somalia',                             'SO',  'SO'),
    ('South Africa',                        'ZA',  'ZA'),
    ('South Sudan',                         'SS',  'SS'),
    ('Spain',                               'ES',  'ES'),
    ('Sri Lanka',                           'LK',  'LK'),
    ('Sudan',                               'SD',  'SD'),
    ('Suriname',                            'SR',  'SR'),
    ('Sweden',                              'SE',  'SE'),
    ('Switzerland',                         'CH',  'CH'),
    ('Syria',                               'SY',  'SY'),
    ('Taiwan',                              'TW',  'TW'),
    ('Tajikistan',                          'TJ',  'TJ'),
    ('Tanzania',                            'TZ',  'TZ'),
    ('Thailand',                            'TH',  'TH'),
    ('Timor-Leste',                         'TL',  'TL'),
    ('Togo',                                'TG',  'TG'),
    ('Tonga',                               'TO',  'TO'),
    ('Trinidad and Tobago',                 'TT',  'TT'),
    ('Tunisia',                             'TN',  'TN'),
    ('Turkey',                              'TR',  'TR'),
    ('Turkmenistan',                        'TM',  'TM'),
    ('Tuvalu',                              'TV',  'TV'),
    ('Uganda',                              'UG',  'UG'),
    ('Ukraine',                             'UA',  'UA'),
    ('United Arab Emirates',                'AE',  'AE'),
    ('United Kingdom',                      'GB',  'GB'),
    ('United States',                       'US',  'US'),
    ('Uruguay',                             'UY',  'UY'),
    ('Uzbekistan',                          'UZ',  'UZ'),
    ('Vanuatu',                             'VU',  'VU'),
    ('Vatican City',                        'VA',  'VA'),
    ('Venezuela',                           'VE',  'VE'),
    ('Vietnam',                             'VN',  'VN'),
    ('Yemen',                               'YE',  'YE'),
    ('Zambia',                              'ZM',  'ZM'),
    ('Zimbabwe',                            'ZW',  'ZW')
) v (Jurisdiction, Abbreviation, ISO2)
WHERE NOT EXISTS (
    SELECT 1 FROM dbo.DIM_Jurisdiction x
    WHERE x.Jurisdiction = v.Jurisdiction AND x.Juris_Type = 'Country'
);
GO

-- ------------------------------------------------------------
-- US States and Territories  (Parent = United States)
-- ------------------------------------------------------------

DECLARE @US_ID INT = (SELECT ID FROM dbo.DIM_Jurisdiction WHERE Jurisdiction = 'United States' AND Juris_Type = 'Country');

INSERT INTO dbo.DIM_Jurisdiction (Jurisdiction, Abbreviation, Juris_Type, Parent_ID, FIPS)
SELECT v.Jurisdiction, v.Abbreviation, v.Juris_Type, @US_ID, v.FIPS
FROM (VALUES
    ('Alabama',                'AL', 'State',     '01'),
    ('Alaska',                 'AK', 'State',     '02'),
    ('Arizona',                'AZ', 'State',     '04'),
    ('Arkansas',               'AR', 'State',     '05'),
    ('California',             'CA', 'State',     '06'),
    ('Colorado',               'CO', 'State',     '08'),
    ('Connecticut',            'CT', 'State',     '09'),
    ('Delaware',               'DE', 'State',     '10'),
    ('Florida',                'FL', 'State',     '12'),
    ('Georgia',                'GA', 'State',     '13'),
    ('Hawaii',                 'HI', 'State',     '15'),
    ('Idaho',                  'ID', 'State',     '16'),
    ('Illinois',               'IL', 'State',     '17'),
    ('Indiana',                'IN', 'State',     '18'),
    ('Iowa',                   'IA', 'State',     '19'),
    ('Kansas',                 'KS', 'State',     '20'),
    ('Kentucky',               'KY', 'State',     '21'),
    ('Louisiana',              'LA', 'State',     '22'),
    ('Maine',                  'ME', 'State',     '23'),
    ('Maryland',               'MD', 'State',     '24'),
    ('Massachusetts',          'MA', 'State',     '25'),
    ('Michigan',               'MI', 'State',     '26'),
    ('Minnesota',              'MN', 'State',     '27'),
    ('Mississippi',            'MS', 'State',     '28'),
    ('Missouri',               'MO', 'State',     '29'),
    ('Montana',                'MT', 'State',     '30'),
    ('Nebraska',               'NE', 'State',     '31'),
    ('Nevada',                 'NV', 'State',     '32'),
    ('New Hampshire',          'NH', 'State',     '33'),
    ('New Jersey',             'NJ', 'State',     '34'),
    ('New Mexico',             'NM', 'State',     '35'),
    ('New York',               'NY', 'State',     '36'),
    ('North Carolina',         'NC', 'State',     '37'),
    ('North Dakota',           'ND', 'State',     '38'),
    ('Ohio',                   'OH', 'State',     '39'),
    ('Oklahoma',               'OK', 'State',     '40'),
    ('Oregon',                 'OR', 'State',     '41'),
    ('Pennsylvania',           'PA', 'State',     '42'),
    ('Rhode Island',           'RI', 'State',     '44'),
    ('South Carolina',         'SC', 'State',     '45'),
    ('South Dakota',           'SD', 'State',     '46'),
    ('Tennessee',              'TN', 'State',     '47'),
    ('Texas',                  'TX', 'State',     '48'),
    ('Utah',                   'UT', 'State',     '49'),
    ('Vermont',                'VT', 'State',     '50'),
    ('Virginia',               'VA', 'State',     '51'),
    ('Washington',             'WA', 'State',     '53'),
    ('West Virginia',          'WV', 'State',     '54'),
    ('Wisconsin',              'WI', 'State',     '55'),
    ('Wyoming',                'WY', 'State',     '56'),
    ('District of Columbia',   'DC', 'Territory', '11'),
    ('American Samoa',         'AS', 'Territory', '60'),
    ('Guam',                   'GU', 'Territory', '66'),
    ('Northern Mariana Islands','MP', 'Territory', '69'),
    ('Puerto Rico',            'PR', 'Territory', '72'),
    ('U.S. Virgin Islands',    'VI', 'Territory', '78')
) v (Jurisdiction, Abbreviation, Juris_Type, FIPS)
WHERE NOT EXISTS (
    SELECT 1 FROM dbo.DIM_Jurisdiction x
    WHERE x.Jurisdiction = v.Jurisdiction AND x.Parent_ID = @US_ID
);
GO

-- ------------------------------------------------------------
-- Canadian Provinces and Territories  (Parent = Canada)
-- ------------------------------------------------------------

DECLARE @CA_ID INT = (SELECT ID FROM dbo.DIM_Jurisdiction WHERE Jurisdiction = 'Canada' AND Juris_Type = 'Country');

INSERT INTO dbo.DIM_Jurisdiction (Jurisdiction, Abbreviation, Juris_Type, Parent_ID)
SELECT v.Jurisdiction, v.Abbreviation, v.Juris_Type, @CA_ID
FROM (VALUES
    ('Alberta',                  'AB', 'Province'),
    ('British Columbia',         'BC', 'Province'),
    ('Manitoba',                 'MB', 'Province'),
    ('New Brunswick',            'NB', 'Province'),
    ('Newfoundland and Labrador','NL', 'Province'),
    ('Nova Scotia',              'NS', 'Province'),
    ('Ontario',                  'ON', 'Province'),
    ('Prince Edward Island',     'PE', 'Province'),
    ('Quebec',                   'QC', 'Province'),
    ('Saskatchewan',             'SK', 'Province'),
    ('Northwest Territories',    'NT', 'Territory'),
    ('Nunavut',                  'NU', 'Territory'),
    ('Yukon',                    'YT', 'Territory')
) v (Jurisdiction, Abbreviation, Juris_Type)
WHERE NOT EXISTS (
    SELECT 1 FROM dbo.DIM_Jurisdiction x
    WHERE x.Jurisdiction = v.Jurisdiction AND x.Parent_ID = @CA_ID
);
GO
