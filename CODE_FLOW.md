# sdn_match_v2.py — Processing Flow

This document walks through `sdn_match_v2.py` from start to finish, in the
order code actually executes. Each numbered section is one step in the
flow, with a source excerpt and notes on any Python syntax that might be
unfamiliar if you're coming from C#/Java/T-SQL/etc.

Throughout, "SDN" = the OFAC Specially Designated Nationals database
(`SDN.dbo.sdnEntry` and related tables); "input records" = the people/
entities being screened against SDN.

---

## 1. Entry point and argument parsing

Python has no `Main()` method — a file is just executed top to bottom. The
convention for "only run this if the file is executed directly (not
imported)" is:

```python
if __name__ == '__main__':
    main()
```

`main()` ([sdn_match_v2.py:3814](sdn_match_v2.py)) starts by building the
command-line argument parser with the standard library's `argparse`:

```python
ap = argparse.ArgumentParser(
    description="SDN matching v2 -- names + addresses, full-text + word-level"
)
inp = ap.add_mutually_exclusive_group(required=True)
inp.add_argument('--input-csv', metavar='PATH', help='Input CSV file ...')
inp.add_argument('--input-screening', action='store_true',
                 help='Load input from a ScreeningInput-schema table ...')
...
args = ap.parse_args()
```

**Python notes:**
- `add_mutually_exclusive_group(required=True)` — exactly one of
  `--input-csv`, `--input-table`, `--input-principals`, `--input-screening`
  must be supplied; argparse errors out otherwise. This is the equivalent
  of validating "exactly one of these flags" yourself in C# — argparse
  does it for you.
- `action='store_true'` — a flag with no value (like `/silent` in a
  Windows CLI); `args.input_screening` becomes `True`/`False`.
- After parsing, every value is available as an attribute:
  `args.input_csv`, `args.sdn_server`, etc. (argparse converts dashes to
  underscores).

---

## 2. Configuration loading

`load_v2_config()` ([sdn_match_v2.py:205](sdn_match_v2.py)) reads
`sdn_match_v2.cfg` (an INI file, falling back to `sdn_match.cfg` for one
section) using `configparser` — Python's built-in INI reader, similar to
`.NET`'s `ConfigurationManager` / `appsettings.json`.

```python
cfg = configparser.ConfigParser()
cfg.read([path, 'sdn_match.cfg'])

scores = {}
for key, val in cfg['MatchTypeScores'].items():
    matched = label_map.get(key.strip().lower(), key.strip())
    scores[matched] = int(val)
```

It returns a single `dict` (Python's hash map / `Dictionary<string,object>`)
containing:

- `scores` — match-type name → integer score
- `keep_chars` — punctuation characters to preserve during normalization
- `min_jw_addr` — minimum Jaro-Winkler similarity (0–1) to count an address
  field as a match
- `use_phonetic`, `jw_name_threshold`, `jw_org_threshold`,
  `jw_org_aka_threshold` — various match thresholds

**Python notes:**
- `cfg['MatchTypeScores'].items()` — `.items()` iterates a dict as
  `(key, value)` pairs, like `foreach (var kvp in dict)` in C#.
- `dict.get(key, default)` — returns `default` if `key` isn't present,
  avoiding a `KeyNotFoundException`-style error.

---

## 3. Normalization helpers

Before any comparison happens, both SDN data and input data are run through
shared "normalization" functions so that, e.g., `"123 Main St."` and
`"123 MAIN STREET"` compare as equal. These are plain functions defined near
the top of the file (around [sdn_match_v2.py:272](sdn_match_v2.py)):

```python
def normalize(s: str, strip_pat: re.Pattern) -> str:
    """Remove disallowed punctuation, collapse whitespace, uppercase."""
    if not s:
        return ''
    s = strip_pat.sub('', s).strip()
    return _WHITESPACE.sub(' ', s).upper()
```

- `normalize()` — uppercase, strip punctuation, collapse multiple spaces.
- `expand_address_nm()` — additionally expands USPS abbreviations
  (`ST` → `STREET`) using a map loaded from the database.
- `expand_entity_nm()` — expands a trailing **entity suffix**
  (`LLC` → `LIMITED LIABILITY COMPANY`) using the hardcoded
  `ENTITY_SUFFIX_MAP` dict, then normalizes.
- `normalize_state()` / `normalize_country()` — expand 2-letter state/
  country codes to full names.

**Python notes:**
- `s: str` and `-> str` are **type hints** — documentation for humans and
  tools, but Python does not enforce them at runtime (unlike C#). They're
  useful for understanding what a function expects/returns.
- `re.Pattern` objects come from `re.compile(...)` — a precompiled regular
  expression, similar to `System.Text.RegularExpressions.Regex` in C#.
  `_build_strip_pattern()` builds one of these once, and it's passed
  around as a parameter rather than recompiled on every call (recompiling
  a regex repeatedly is wasteful).

---

## 4. Connecting to the databases

`_conn_str()` ([sdn_match_v2.py:3778](sdn_match_v2.py)) builds an ODBC
connection string:

```python
def _conn_str(server: str, database: str) -> str:
    import os
    user = os.environ.get('SQL_USER')
    pwd  = os.environ.get('SQL_PASSWORD')
    driver = os.environ.get('SQL_DRIVER', 'ODBC Driver 17 for SQL Server')
    if user and pwd:
        return (f"DRIVER={{{driver}}};SERVER={server};DATABASE={database};"
                f"UID={user};PWD={pwd};Encrypt=yes;TrustServerCertificate=no;"
                f"Connection Timeout=30;")
    return (f"DRIVER={{{driver}}};"
            f"SERVER={server};DATABASE={database};Trusted_Connection=yes;")
```

Two separate connection strings are built, one per database:

```python
sdn_cs = _conn_str(args.sdn_server, args.sdn_database)   # SDN
out_cs = _conn_str(args.out_server,  args.out_database)  # SDNReporting
```

Connections are opened with `pyodbc.connect(...)`, used inside a
`with` block, and closed automatically when the block exits — even if an
exception happens. This is the same pattern as C#'s
`using (var conn = new SqlConnection(...))`.

```python
with pyodbc.connect(sdn_cs) as sdn_conn:
    name_idx = load_sdn_names(sdn_conn, strip_pat)
    addresses, addr_word_index = load_sdn_addresses(sdn_conn, abbrev_map, strip_pat)
    ...
```

**Python notes:**
- `f"DRIVER={{{driver}}};..."` — an **f-string**: `{driver}` is replaced
  with the variable's value. The doubled `{{` / `}}` are *literal* braces
  (needed because the ODBC driver name itself must be wrapped in `{ }`).
- `os.environ.get('SQL_USER')` — reads an environment variable, returning
  `None` if it's not set (like `Environment.GetEnvironmentVariable` in C#).

---

## 5. Loading SDN reference data

With the SDN connection open, four loader functions pull everything needed
for matching into memory (Python dicts/lists) — there's no per-input-record
querying of SDN during the matching loop; everything is held in RAM for
speed.

### 5.0 `load_abbrev_map()` ([sdn_match_v2.py:342](sdn_match_v2.py))

The first thing loaded, since `load_sdn_addresses()` (5.2) needs it. Reads
`dbo.Address_Abbreviation` from the SDN database and builds a dict mapping
USPS-style abbreviations to their expansions (e.g. `ST` → `STREET`,
`AVE` → `AVENUE`), used by `expand_address_nm()` (Section 3) to normalize
both SDN and input addresses.

```python
with pyodbc.connect(sdn_cs) as sdn_conn:
    abbrev_map = load_abbrev_map(sdn_conn)
    name_idx = load_sdn_names(sdn_conn, strip_pat)
    ...
```

### 5.1 `load_sdn_names()` ([sdn_match_v2.py:424](sdn_match_v2.py))

Loads every `sdnEntry` (Individual or Entity) plus their AKA names. Returns
a dict (`name_idx`) containing, among other things:

- `sdn_entry_map` — SDN UID → `(first_name, last_name, entity_type)`
- `aka_by_sdn` — SDN UID → list of `(aka_uid, first_name, last_name, category)`
- `entity_aka_by_sdn` — SDN UID → list of `(aka_uid, org_name, category)`
- word-index dicts used to quickly find *candidate* SDN entries that share a
  word with the input name (avoids comparing every input against every SDN
  record one-by-one for the initial pass)

### 5.2 `load_sdn_addresses()` ([sdn_match_v2.py:571](sdn_match_v2.py))

Loads every SDN address row, normalizes city/state/postal/country, and
returns `(addresses, addr_word_index)` — `addresses` is a plain Python
`list` of `SdnAddress` objects (see Section 6 for what a "dataclass" is).

### 5.3 `load_sdn_remarks()` ([sdn_match_v2.py:625](sdn_match_v2.py))

Parses the free-text `remarks` field on each `sdnEntry` for `"Linked to: ..."`
phrases and phone numbers using regular expressions:

```python
_LINKED_TO_PAT = re.compile(
    r'Linked\s+to:\s*(.+?)(?=\s*[;.]|\s*Linked\s+to:|$)', re.IGNORECASE)
```

Returns:

- `linked_to_by_uid` — SDN UID → list of
  `(occurrence_number, raw_text, lowercase_text, suffix_expanded_text)`
- `phones_by_uid` — SDN UID → list of `(raw_phone, digits_only)`
- `lt_word_index` / `phone_last7_idx` — lookup indexes for candidate
  pre-filtering, similar in spirit to the word indexes from Section 5.1

**Python notes:**
- `re.compile(r'...')` — the `r''` prefix means **raw string**: backslashes
  are literal, not escape characters. Essential for regex patterns
  (`r'\s+'` means "the regex `\s+`", not a tab character followed by `+`).
- `(?=...)` in the pattern is a **lookahead** — "match up to here, but don't
  consume this part." Same concept as in .NET regex.
- Dictionaries returned here use plain Python `dict`, but during
  construction the code often uses `collections.defaultdict(set)` /
  `defaultdict(list)` — a dict that auto-creates an empty `set`/`list` the
  first time a new key is accessed, so you can write
  `word_index[word].add(i)` without first checking `if word not in word_index`.

---

## 6. Loading input records

Depending on which `--input-*` flag was passed, one of four loader
functions returns a `list` of `InputRecord` objects:

- `load_input_csv(path)` — reads a CSV file with `csv.DictReader`
- `load_input_db(server, database, table)` — reads an arbitrary table/view
- `load_input_principals(...)` — reads `California.dbo.Principals_Alpha`
- `load_input_screening(...)` — reads `SDN.dbo.ScreeningInput` (the
  default for production runs)

### `InputRecord` — a dataclass

```python
@dataclass
class InputRecord:
    entity_type:  Optional[str] = None   # Individual | Entity | Unknown
    entity_name:  Optional[str] = None
    first_name:   Optional[str] = None
    last_name:    Optional[str] = None
    address1:     Optional[str] = None
    city:         Optional[str] = None
    country:      Optional[str] = None
    phone:        Optional[str] = None
    # Normalized (filled in later by normalize_input)
    entity_name_nm:  str = ''
    first_name_nm:   str = ''
    ...
```

**Python notes:**
- `@dataclass` is a **decorator** — a shorthand that auto-generates the
  constructor (`__init__`), so `InputRecord(first_name='John', last_name='Smith')`
  works without writing a constructor by hand. Roughly equivalent to a C#
  record type or a POCO with an auto-generated constructor.
- `Optional[str] = None` — the field is a `str` *or* `None`, defaulting to
  `None`. `Optional[X]` is shorthand for "`X` or `None`" (from the `typing`
  module).
- Fields ending in `_nm` ("normalized") are computed later — at load time
  they're empty strings (`''`).

`load_input_screening()` ([sdn_match_v2.py:1605](sdn_match_v2.py)) is the
most involved loader: each `ScreeningInput` row can produce **one or two**
`InputRecord`s — one for the entity/individual itself, and (if principal
contact fields are populated) a second `InputRecord` representing that
contact as an Individual, sharing the same address.

---

## 7. Normalizing input records

Every `InputRecord` is passed through `normalize_input()`
([sdn_match_v2.py:403](sdn_match_v2.py)), which fills in all the `_nm`
fields using the functions from Section 3:

```python
def normalize_input(rec: InputRecord, abbrev_map: dict,
                    strip_pat: re.Pattern) -> InputRecord:
    rec.entity_name_nm  = _ph_norm_name(expand_entity_nm(_s(rec.entity_name) or '', strip_pat))
    rec.first_name_nm   = _ph_norm_name(normalize(_s(rec.first_name) or '', strip_pat))
    rec.address1_nm     = expand_address_nm(_s(rec.address1) or '', abbrev_map, strip_pat)
    rec.country_nm      = normalize_country(_s(rec.country) or '', strip_pat)
    rec.phone_nm        = re.sub(r'\D', '', _s(rec.phone) or '')
    return rec
```

**Python notes:**
- `_s(v) or ''` — `_s()` converts `None`/blank to `None`; `... or ''` then
  turns `None` into an empty string. This `x or default` idiom is very
  common in Python (similar to `x ?? default` in C#, though Python's `or`
  also treats empty strings/0/empty lists as "falsy").
- `_ph_norm_name()` replaces `"PH"` with `"F"` in already-uppercased names,
  so `"PHILLIPS"` and `"FILLIPS"` compare as equal — a phonetic tweak
  applied only to person/entity *names*, never addresses.
- `re.sub(r'\D', '', ...)` — replace every **non-digit** character (`\D`)
  with nothing, leaving only digits — used to build a phone "digits only"
  key.

---

## 8. Building entity org/AKA lookup maps

For Pass 3 (Entity name matching) and Pass 3b (Entity AKA matching), two
dicts are built once from the SDN data already loaded in Section 5:

```python
entity_org_map = {}   # uid -> (raw_name, suffix_expanded_normalized_name)
for uid, fn, ln, sdt in all_sdn_entries:
    if sdt != 'Entity':
        continue
    raw_ln  = ln or ''
    norm_ln = _ph_norm_name(expand_entity_nm(raw_ln, strip_pat))
    entity_org_map[uid] = (raw_ln if ln else None, norm_ln)
```

`entity_aka_norm` is built the same way but keyed by
`(sdn_uid, aka_uid)` tuples, one entry per Entity AKA name.

**Python notes:**
- `for uid, fn, ln, sdt in all_sdn_entries:` — **tuple unpacking** in a
  loop. Each element of `all_sdn_entries` is itself a 4-item tuple
  `(uid, fn, ln, sdt)`, and the loop unpacks all four into separate
  variables on each iteration — similar to `foreach (var (uid, fn, ln, sdt) in ...)`
  if you've used C# tuple deconstruction.
- A `dict` literal `{}` here is being built up with a `for` loop (an
  "accumulator" pattern) rather than a dict comprehension, because the
  `continue` makes a comprehension awkward.

---

## 9. (Optional) Synthetic "dummy" record generation

If `--dummy-records N` is passed, `generate_dummy_records()`
([sdn_match_v2.py:3156](sdn_match_v2.py)) creates `N` synthetic
`InputRecord`s by taking real SDN names/addresses and *perturbing* them
(`_perturb_to_jw_range`) so they land in a 75–99% Jaro-Winkler similarity
band — useful for testing that "near miss" matches are surfaced correctly.

```python
ind_pool = [(uid, _nm(fn), _nm(ln))
            for uid, (fn, ln, _) in sdn_entry_map.items()
            if len(_nm(fn)) >= 3 and len(_nm(ln)) >= 3]
```

**Python notes:**
- This is a **list comprehension**: `[expression for item in iterable if condition]`.
  It's equivalent to:
  ```python
  ind_pool = []
  for uid, (fn, ln, _) in sdn_entry_map.items():
      if len(_nm(fn)) >= 3 and len(_nm(ln)) >= 3:
          ind_pool.append((uid, _nm(fn), _nm(ln)))
  ```
  but more concise. You'll see this pattern throughout the file (and in
  set/dict form: `{...}` comprehensions build sets/dicts the same way).
- `(fn, ln, _)` — the underscore `_` is the conventional name for "a value
  I'm required to unpack but don't need." Here, the third tuple element
  (`entity_type`) is discarded.
- Dummy records are written back to `ScreeningInput` (so they show up
  alongside real data) via `insert_dummy_screening_rows()`, then appended
  to `input_records` in memory for this run.

---

## 10. Cache precompute (Passes 1, 2, 3, 3b)

This is the performance-critical step: rather than comparing each input
record against every SDN record one at a time inside the main loop, the
script first computes **unique** input keys, then scores each unique key
against all of SDN **once**, caching the results.

```python
unique_indiv_keys  = list({
    (r.first_name_nm, r.middle_name_nm, r.last_name_nm)
    for r in input_records if r.entity_type != 'Entity'
})
unique_entity_keys = list({
    r.entity_name_nm
    for r in input_records
    if r.entity_type != 'Individual' and r.entity_name_nm
})
```

**Python notes:**
- `{... for r in input_records if ...}` (curly braces, no `:`) is a **set
  comprehension** — like a list comprehension but de-duplicates
  automatically. Wrapping it in `list(...)` converts the de-duplicated set
  back to a list. This is how "find all distinct (FN, MN, LN) combinations
  across thousands of input rows" is done in two lines.

Two strategies compute the cache:

### 11.1 DuckDB strategy (default)

`_precompute_duckdb()` ([sdn_match_v2.py:3438](sdn_match_v2.py)) loads the
unique keys and the SDN reference data into an **in-memory DuckDB database**
(`duckdb.connect()`), then runs SQL queries that:

- "Block" candidates by first-letter and Double-Metaphone/Soundex code
  (so e.g. "Smith" is only compared against SDN names that also start with
  "S" and sound similar) — this cuts the comparison space by ~99%.
- Compute Jaro-Winkler similarity in bulk via DuckDB's
  `jaro_winkler_similarity()` SQL function.

This is dramatically faster than comparing every input name against every
SDN name (an O(N×M) cross-product) because the blocking step eliminates the
vast majority of pairs before any similarity scoring happens.

### 11.2 Parallel-worker fallback (`--no-duckdb`)

If DuckDB isn't available or `--no-duckdb` is passed, the same unique keys
are split into chunks and scored across multiple OS processes using
`ProcessPoolExecutor` — Python's equivalent of `Parallel.ForEach` with
separate processes (not threads, because of Python's Global Interpreter
Lock — CPU-bound work doesn't parallelize across threads, only processes).

```python
with ProcessPoolExecutor(max_workers=n_workers,
                         initializer=_worker_init,
                         initargs=_init_args) as _pool:
    _futs = [(_pool.submit(_score_indiv_keys_batch, b), 'indiv')
             for b in indiv_batches]
    for _fut, _kind in _futs:
        _br = _fut.result()
        ...
```

**Python notes:**
- `ProcessPoolExecutor(...).submit(fn, args)` returns a `Future`
  immediately; `.result()` blocks until that worker process finishes and
  returns its value — similar to `Task<T>` / `await` in C#, but
  `.result()` is a blocking call rather than `async`/`await`.
- `initializer=_worker_init, initargs=_init_args` — each worker process
  runs `_worker_init(*_init_args)` once when it starts, storing the SDN
  reference data in a module-level dict (`_WK`) *inside that process*, so
  it isn't re-sent for every batch (cross-process data transfer is
  expensive — this amortizes the cost).

Either way, the result is two dicts:

- `indiv_name_cache` — `(fn_nm, mn_nm, ln_nm)` → match results against all
  SDN Individuals (Pass 1) and Individual AKAs (Pass 2)
- `entity_name_cache` — `entity_name_nm` → match results against all SDN
  Entities (Pass 3) and Entity AKAs (Pass 3b)

---

## 11. Phase 1 — per-record fan-out and SQL writes

This is the main loop — one iteration per `InputRecord`
([sdn_match_v2.py:4173](sdn_match_v2.py)):

```python
for input_record_id, rec in enumerate(input_records, 1):
    fn_nm = rec.first_name_nm
    mn_nm = rec.middle_name_nm
    ln_nm = rec.last_name_nm
    ...
```

**Python notes:**
- `enumerate(input_records, 1)` — yields `(1, rec1), (2, rec2), ...` —
  i.e. a 1-based running counter alongside each item. This counter becomes
  `Input_Record_ID` / `ScreeningInput_ID` in the output tables.

For each record, depending on `rec.entity_type`, the relevant caches from
Section 10 are looked up (already computed — this is just a dict lookup,
`O(1)`), and the **per-record raw values** (the actual submitted name,
spelled however the source system spelled it) are spliced onto the cached
SDN-side results to build output rows. Each pass appends rows to a
"batch" list:

| Pass | What's compared | Output table | Batch variable |
|---|---|---|---|
| 1 | Input person name vs. SDN Individuals | `MatchingResults_Person_Full` | `full_batch` |
| 2 | Input person name vs. SDN Individual AKAs | `MatchingResults_AKA` | `aka_batch` |
| 3 | Input entity name vs. SDN Entities | `MatchingResults_OrgName` | `org_batch` |
| 3b | Input entity name vs. SDN Entity AKAs | `MatchingResults_OrgName_AKA` | `org_aka_batch` |
| 5 | Input name vs. SDN "Linked to:" remarks | `MatchingResults_LinkedTo` | `linked_to_batch` |
| 6 | Input phone vs. SDN phone numbers | `MatchingResults_Phone` | `phone_batch` |

(Pass 4 — addresses — runs separately in Phase 2, Section 12, because it
only needs to run for records that already matched something in Phase 1.)

Passes 3/3b/5 also apply the **entity match gate**
(`_entity_match_gate_v2()`, [sdn_match_v2.py:2818](sdn_match_v2.py)) — a
Python function (not SQL) that decides whether a high string-similarity
score is *actually* a plausible entity-name match, using rules about
matching entity suffixes (LLC/Inc/Corp/etc.) and how many of the remaining
words also match.

### Flushing to SQL

Every `args.flush_interval` records (default 50), each batch list is
written to its SQL table and then cleared:

```python
processed += 1
if processed % args.flush_interval == 0:
    flush_full_results(out_conn, s, full_batch)
    flush_aka_results(out_conn, s, aka_batch)
    ...
    full_batch = []
    aka_batch  = []
    ...
```

The flush functions (e.g. `flush_full_results()`,
[sdn_match_v2.py:1430](sdn_match_v2.py)) use
`cursor.fast_executemany = True` with `cursor.executemany(sql, rows)` —
pyodbc's bulk-insert mode, which sends many rows per network round-trip
instead of one `INSERT` per row.

**Python notes:**
- `processed % args.flush_interval == 0` — `%` is the modulo operator,
  same as in C#: "every Nth record."
- `full_batch = []` re-binds the name `full_batch` to a brand-new empty
  list; the old list (already handed to `flush_full_results`) is no longer
  referenced and gets garbage-collected.

At the end of Phase 1, any input record that didn't appear in **any** of
the six match tables gets a single row in `MatchingResults_NoMatch` via
`flush_no_match_log()`.

---

## 12. Phase 2 — Address comparison (Pass 4)

Only records that matched in Phase 1 (`matched_input_pairs`) are processed
here ([sdn_match_v2.py:4538](sdn_match_v2.py)). For each such record, its
address is compared against **every** SDN address using
`_find_street_jw_match()` (street-line comparison) and `_geo_addr_score()`
(city/state/postal/country comparison):

```python
for input_record_id, rec in matched_input_pairs:
    inp_mailing = ' '.join(filter(None, [rec.address1_nm,
                                          rec.address2_nm,
                                          rec.address3_nm]))
    addr_key = (rec.address1_nm, rec.address2_nm, rec.address3_nm,
                rec.city_nm, rec.region_nm, rec.postal_code_nm, rec.country_nm)

    if addr_key not in addr_score_cache:
        ... # score against every SdnAddress, cache the result
    for _sdn in addr_score_cache[addr_key]:
        addr_batch.append((...))
```

Results go to `MatchingResults_Address`, flushed the same way as Phase 1
(every `flush_interval` records).

**Python notes:**
- `' '.join(filter(None, [a, b, c]))` — `filter(None, iterable)` drops any
  `None`/empty-string items; `' '.join(...)` then concatenates the
  survivors with single spaces. A compact way to say "combine these three
  optional address lines, skipping blanks."
- `addr_key` is a **tuple** used as a dict key — tuples are immutable and
  hashable, so they can be dict keys (lists cannot). This is the same
  caching-by-input-value pattern as Section 10/11.

---

## 13. Summary table population

After both phases finish, `populate_matching_summary()`
([sdn_match_v2.py:2374](sdn_match_v2.py) area) runs two large SQL `INSERT
... SELECT` statements (`_SUMMARY_PERSON_INSERT_SQL`,
`_SUMMARY_ORG_INSERT_SQL`) directly against the output database:

```python
with pyodbc.connect(out_cs) as out_conn:
    update_run(out_conn, s, run_id, len(input_records), total_rows, 0, 0)
    person_summary_rows, org_summary_rows = populate_matching_summary(out_conn, s, run_id)
```

These statements use Common Table Expressions (CTEs) to pull the
**best (`MAX`) match per `(Input_Record_ID, SDN_UID)`** from each of the
Phase 1/2 result tables, `UNION` those combinations together, then `LEFT
JOIN` back to populate one row per input/SDN pair in
`Matching_Summary_Person` / `Matching_Summary_Org` — including the actual
input and SDN name/city/country values for each match type.

**Python notes:**
- This step is pure SQL — the Python code's job is just to substitute the
  schema name and `run_id` into the SQL string template
  (`.format(...)` / `{s}` placeholders) and execute it via
  `cursor.execute(sql, run_id, run_id, ...)`. No row-by-row Python
  processing happens here.

---

## 14. Final reporting

The very last thing `main()` does is print a summary to the console:

```python
print(f"\nDone.  run_id={run_id}")
print(f"  {len(input_records):,} input records")
print(f"  {total_full_rows:,} rows → MatchingResults_Person_Full")
...
print(f"  {person_summary_rows:,} rows → Matching_Summary_Person")
print(f"  {org_summary_rows:,} rows → Matching_Summary_Org")
```

**Python notes:**
- `f"{value:,}"` — the `:,` format spec adds thousands separators
  (`12345` → `12,345`), same idea as `value.ToString("N0")` in C#.

---

## Quick reference — where things live

- **Configuration**: `sdn_match_v2.cfg` (thresholds, scores)
- **Entity suffix map**: `ENTITY_SUFFIX_MAP` dict, top of file
- **Per-record data shape**: `InputRecord` dataclass
- **SDN address shape**: `SdnAddress` dataclass
- **DDL for every output table**: `_DDL_*` string constants (e.g.
  `_DDL_FULL`, `_DDL_ORG`, `_DDL_LINKED_TO`, `_DDL_SUMMARY_PERSON`)
- **INSERT statements**: `_*_INSERT_SQL` string constants, paired 1:1 with
  the DDL constants above
- **Flush (bulk insert) functions**: `flush_*_results(conn, schema, rows)`
