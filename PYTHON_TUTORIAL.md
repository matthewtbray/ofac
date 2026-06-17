# Learning Python Through `sdn_match_v2.py`

This document teaches Python concepts using real examples pulled from
`sdn_match_v2.py`. It assumes you already understand general programming
concepts (variables, loops, functions, classes, etc. from another language)
and focuses on **Python's specific syntax and idioms**.

Concepts are ordered from basic to advanced. Each one includes:

- **Concept** — what it is and the general syntax
- **Example** — real code from the project, with a line reference
- **How it's used here** — what the example is doing and why it's written
  this way

---

## 1. Variables, basic types, and `None`

**Concept:** Python is dynamically typed — you don't declare a variable's
type; it's inferred from the value assigned. The basic built-in types are
`str` (text), `int` (whole numbers), `float` (decimals), `bool` (`True`/
`False`), and the special value `None` (Python's "no value" — like `null`
in C#/Java).

**Example** ([sdn_match_v2.py:3344](sdn_match_v2.py)):

```python
_WK: dict = {}   # populated by _worker_init in each worker process
```

**How it's used here:** `_WK` is a variable holding an empty dictionary.
The `: dict` part is an optional **type hint** (covered in Section 11) —
it tells a reader (and tools like type checkers) that `_WK` is meant to
hold a `dict`, but Python won't stop you from assigning something else to
it at runtime.

---

## 2. `f-strings` (formatted string literals)

**Concept:** Prefixing a string with `f` lets you embed expressions directly
inside `{ }` braces. This is Python's equivalent of C#'s
`$"Hello {name}"` or string interpolation in JavaScript template literals.

**Example** ([sdn_match_v2.py:528](sdn_match_v2.py)):

```python
print(f"  {len(sdn_rows):,} sdnEntry + {len(aka_rows):,} akaList rows indexed.")
```

**How it's used here:** `len(sdn_rows)` and `len(aka_rows)` are evaluated,
and the `:,` after each one is a **format spec** that adds thousands
separators (e.g. `12345` becomes `12,345` — similar to `.ToString("N0")` in
C#). The result is a single readable status line printed to the console.

A second example with a percentage format spec
([sdn_match_v2.py:3894](sdn_match_v2.py)):

```python
print(f"Org name thresholds: JW={jw_org_threshold:.0%}")
```

Here `:.0%` converts a fraction like `0.75` into `"75%"`.

---

## 3. Lists

**Concept:** A list (`[]`) is an ordered, mutable, growable collection —
Python's equivalent of `List<T>` in C# or `ArrayList`/`array` in other
languages. Unlike arrays in statically-typed languages, a Python list can
hold mixed types, though in practice this codebase keeps them uniform.

**Example** ([sdn_match_v2.py:3123-3130](sdn_match_v2.py)):

```python
ALPHA = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
# Only mutate non-space positions
mutable = [i for i, c in enumerate(s) if c != ' ']
if not mutable:
    return s
# Prefer the back two-thirds so the prefix stays intact
back = [i for i in mutable if i >= max(1, len(s) // 3)]
pool = back if back else mutable
```

**How it's used here:** `mutable` and `back` are lists of integer
*positions* (indexes) within the string `s`. `pool = back if back else
mutable` is a **conditional expression** (see Section 7) that falls back to
`mutable` if `back` ended up empty. `//` is **integer (floor) division** —
`len(s) // 3` rounds down to a whole number, unlike `/` which always
produces a `float`.

---

## 4. Dictionaries

**Concept:** A dict (`{}`) is Python's hash map — `Dictionary<TKey, TValue>`
in C#. Keys must be **immutable** (strings, numbers, tuples — but not
lists). Access with `d[key]`, or safely with `d.get(key, default)`.

**Example** ([sdn_match_v2.py:97-120](sdn_match_v2.py)):

```python
ENTITY_SUFFIX_MAP = {
    'LLC':   'Limited Liability Company',
    'LC':    'Limited Company',
    'INC':   'Incorporated',
    'CORP':  'Corporation',
    'LTD':   'Limited',
    ...
}
```

**How it's used here:** This is a module-level constant dict mapping
business-entity abbreviations to their full names. It's used like this
([sdn_match_v2.py:321-322](sdn_match_v2.py)):

```python
if last_clean in ENTITY_SUFFIX_MAP:
    tokens[-1] = ENTITY_SUFFIX_MAP[last_clean]
```

`in` checks whether a key exists in the dict (an `O(1)` lookup, much faster
than scanning a list). `tokens[-1]` is **negative indexing** — `-1` always
means "the last element," `-2` the second-to-last, etc.

---

## 5. Tuples and tuple unpacking

**Concept:** A tuple (`()`) is like a list but **immutable** — once created,
its contents can't change. Because they're immutable, tuples can be used as
dict keys (lists can't). Tuples are also the natural way to return or pass
around "a fixed group of related values."

"Unpacking" lets you assign each element of a tuple (or any iterable) to its
own variable in one statement.

**Example** ([sdn_match_v2.py:592](sdn_match_v2.py)):

```python
for i, (sdn_uid, addr_uid, a1, a2, a3, city, state, postal, country) in enumerate(rows):
```

**How it's used here:** Each row coming back from the database is a 9-item
tuple `(sdn_uid, addr_uid, a1, a2, a3, city, state, postal, country)`. The
`for` loop unpacks that tuple into nine separate variables on every
iteration — no need to write `row[0]`, `row[1]`, etc. `enumerate(rows)`
additionally provides `i`, a running index starting at 0 (covered more in
Section 8).

A simpler unpacking example used as a function's return value
([sdn_match_v2.py:3267](sdn_match_v2.py)):

```python
uid, fn_nm, ln_nm = rng.choice(ind_pool)
```

`rng.choice(ind_pool)` returns one 3-item tuple from the list `ind_pool`,
and that tuple is immediately unpacked into `uid`, `fn_nm`, `ln_nm`.

---

## 6. Conditionals: `if`/`elif`/`else` and conditional expressions

**Concept:** Standard `if`/`elif`/`else` blocks work as in most languages,
but Python also has a compact **conditional expression** (often called a
"ternary"): `value_if_true if condition else value_if_false`.

**Example** ([sdn_match_v2.py:233](sdn_match_v2.py)):

```python
norm = cfg['Normalization'] if 'Normalization' in cfg else {}
```

**How it's used here:** If the config file has a `[Normalization]` section,
use it; otherwise fall back to an empty dict `{}` so that later calls to
`norm.get(...)` don't raise an error. This pattern — "use this value if a
condition holds, otherwise use a safe default" — appears throughout the
config-loading code.

Another example, choosing a score based on whether a match occurred
([sdn_match_v2.py:721](sdn_match_v2.py)):

```python
scores_cfg.get('Direct', 0) if is_match else 0,
```

---

## 7. Loops: `for`, `range`, `while`, and `continue`

**Concept:** Python's `for` loop iterates over the *items* of a sequence
directly (not over index numbers, unless you ask for them via `range()` or
`enumerate()`). `continue` skips to the next iteration, same as in
C-like languages.

**Example** ([sdn_match_v2.py:1373-1374](sdn_match_v2.py)):

```python
for i in range(0, len(rows), batch_size):
    cur.executemany(sql, rows[i:i + batch_size])
```

**How it's used here:** `range(0, len(rows), batch_size)` produces
`0, batch_size, 2*batch_size, ...` up to (but not including) `len(rows)`.
On each iteration, `rows[i:i + batch_size]` is a **slice** — see Section 9 —
that grabs the next chunk of rows. This is how the script inserts thousands
of result rows into SQL Server in manageable batches rather than one giant
`INSERT`.

A `for` loop with `continue`, used to skip blank tokens
([sdn_match_v2.py:296-299](sdn_match_v2.py)):

```python
for i, tok in enumerate(tokens):
    if not tok:
        continue
    mappings = abbrev_map.get(tok.upper())
```

`if not tok:` is true when `tok` is an empty string (Python treats `''` as
"falsy" — see Section 8 for more on this).

---

## 8. Truthiness, `and`/`or`, and the `x or default` idiom

**Concept:** In `if` conditions (and similar contexts), Python treats
certain values as "falsy" even though they aren't literally `False`: `None`,
`0`, `0.0`, `''` (empty string), `[]`, `{}`, and `set()` are all falsy.
Everything else is "truthy." This lets you write `if not s:` instead of
`if s is None or s == '':`.

`or` returns its **first truthy operand** (not just `True`/`False`), which
gives Python a common shorthand for "use this value, or a default if it's
empty/None."

**Example** ([sdn_match_v2.py:405-406](sdn_match_v2.py)):

```python
rec.entity_name_nm  = _ph_norm_name(expand_entity_nm(_s(rec.entity_name) or '', strip_pat))
rec.first_name_nm   = _ph_norm_name(normalize(_s(rec.first_name)   or '', strip_pat))
```

**How it's used here:** `_s(rec.entity_name)` returns either a cleaned-up
string or `None` (see its definition in Section 10). `_s(rec.entity_name)
or ''` evaluates to that string if it's non-empty/truthy, or to `''` if
`_s(...)` returned `None` or an empty string. This guarantees
`expand_entity_nm` always receives a `str`, never `None` — avoiding a
`TypeError` deep inside the normalization functions.

---

## 9. Strings: methods and slicing

**Concept:** Python strings have many built-in methods (`.strip()`,
`.upper()`, `.split()`, `.join()`, etc.), and support **slicing** with
`s[start:stop]` (returns a substring/sublist; `start` is inclusive, `stop`
is exclusive — same convention as most slicing in Python applies to lists,
tuples, and strings alike).

**Example** ([sdn_match_v2.py:310](sdn_match_v2.py)):

```python
return normalize(' '.join(out), strip_pat)
```

**How it's used here:** `out` is a list of word strings; `' '.join(out)`
concatenates them with a single space between each — the reverse of
`'a b c'.split()`. This reads "backwards" compared to many languages (where
you'd expect `out.join(' ')`), because `join` is a method *on the
separator*, not on the list.

Slicing example, reused from Section 7
([sdn_match_v2.py:1374](sdn_match_v2.py)):

```python
rows[i:i + batch_size]
```

If `rows` has 120 items, `batch_size` is 50, and `i` is 100, this returns
`rows[100:150]` — but since there are only 120 items, Python simply returns
the last 20 without raising an "index out of range" error. Slices are always
safe to over-extend past the end of a sequence.

---

## 10. Functions, default arguments, and type hints

**Concept:** Functions are defined with `def name(params) -> return_type:`.
Parameters can have **default values** (`param=default`), making them
optional for the caller. **Type hints** (`param: type`, `-> type`) document
expected types but are *not enforced* by Python at runtime — they're for
readability and for external tools (IDEs, type checkers like `mypy`).
`Optional[X]` (from the `typing` module) means "`X` or `None`".

**Example** ([sdn_match_v2.py:396-400](sdn_match_v2.py)):

```python
def _s(v) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip().rstrip('\r\n')
    return s or None
```

**How it's used here:** `_s` ("safe string") takes any value `v` (no type
hint on the parameter — it could be a string, number, `None`, etc., as
returned from a database driver) and either returns `None` or a cleaned,
non-empty string — never an empty string. The `-> Optional[str]` return
hint documents that callers must handle a possible `None`. `str(v)` converts
any value to its string form (like `.ToString()`), `.strip()` removes
leading/trailing whitespace, and `.rstrip('\r\n')` additionally removes
trailing carriage-return/newline characters that sometimes leak in from
CSV files.

Default-argument example ([sdn_match_v2.py:3112-3114](sdn_match_v2.py)):

```python
def _perturb_to_jw_range(s: str, rng: random.Random,
                          lo: float = 0.75, hi: float = 0.99,
                          max_iter: int = 2000) -> str:
```

**How it's used here:** `lo`, `hi`, and `max_iter` all have defaults, so
most callers can write `_perturb_to_jw_range(name, rng)` and only override
`lo`/`hi`/`max_iter` in special cases.

---

## 11. Modules, `import`, and `try`/`except` for optional dependencies

**Concept:** `import module` brings in an entire module;
`from module import name1, name2` brings in specific names directly. A
`try`/`except` block lets you attempt something that might fail and provide
a fallback — Python's version of `try`/`catch`. `ImportError` is the
exception raised when a module/name can't be found, often because an
optional package isn't installed.

**Example** ([sdn_match_v2.py:76-90](sdn_match_v2.py)):

```python
try:
    from rapidfuzz.distance import JaroWinkler as _rfJW
    def _jaro_winkler_fast(s1: str, s2: str) -> float:
        return _rfJW.normalized_similarity(s1, s2)
    _RAPIDFUZZ = True
except ImportError:
    _jaro_winkler_fast = _jaro_winkler_similarity   # pure-Python fallback
    _RAPIDFUZZ = False

try:
    import duckdb as _duckdb
    _DUCKDB = True
except ImportError:
    _duckdb = None
    _DUCKDB = False
```

**How it's used here:** `rapidfuzz` and `duckdb` are fast, optional
third-party libraries. If they're installed, the script uses their
optimized C++ implementations and sets a flag (`_RAPIDFUZZ`/`_DUCKDB`) to
`True`. If not, it falls back to a slower pure-Python implementation
(`_jaro_winkler_similarity`, imported earlier from `sdn_match.py`) and sets
the flag to `False`. The rest of the code checks these flags later to decide
which code path to take — so the script still runs (just more slowly)
without these optional packages.

`as` renames the imported thing — `import duckdb as _duckdb` lets the code
refer to the module as `_duckdb` instead of `duckdb`.

---

## 12. `try`/`except`/`else` for error handling, and `sys.exit`

**Concept:** Beyond optional imports, `try`/`except` is used generally for
error handling. You can catch specific exception types (e.g.
`ValueError`, raised when a conversion like `int("abc")` fails). `sys.exit(message)`
immediately stops the program and prints `message` to stderr — useful for
"this configuration is invalid, give up now" situations.

**Example** ([sdn_match_v2.py:228-231](sdn_match_v2.py)):

```python
try:
    scores[matched] = int(val)
except ValueError:
    sys.exit(f"Non-integer score for '{key}': {val!r}")
```

**How it's used here:** `int(val)` converts a config-file string to an
integer. If `val` isn't a valid integer (e.g. someone typed `"5.5"` or
`"five"`), `int()` raises `ValueError`, which is caught, and the program
exits with a clear error message. `{val!r}` in the f-string uses the `!r`
**conversion flag**, which calls `repr()` on the value — so a string prints
*with quotes* (e.g. `'five'` instead of `five`), making it obvious in the
error message that `val` is text.

---

## 13. List, set, and generator comprehensions

**Concept:** A comprehension builds a new collection by applying an
expression to each item of an existing iterable, optionally filtering with
`if`. The syntax differs only by the brackets used:

- `[expr for item in iterable if condition]` → **list**
- `{expr for item in iterable if condition}` → **set** (de-duplicated)
- `{key_expr: val_expr for item in iterable}` → **dict**
- `(expr for item in iterable if condition)` → **generator** (lazy — values
  are produced one at a time, not all at once)

**Example — list comprehension** ([sdn_match_v2.py:317](sdn_match_v2.py)):

```python
tokens = [t for t in _WHITESPACE.split(raw.strip()) if t]
```

Splits a string on whitespace, then keeps only the non-empty pieces (`if t`
is the same "truthy string" check from Section 8).

**Example — set comprehension with set union**
([sdn_match_v2.py:2854-2858](sdn_match_v2.py)):

```python
_ENTITY_SUFFIX_PHRASES = sorted(
    {abbr.upper() for abbr in ENTITY_SUFFIX_MAP}
    | {full.upper() for full in ENTITY_SUFFIX_MAP.values()},
    key=lambda p: -len(p.split())
)
```

**How it's used here:** `{abbr.upper() for abbr in ENTITY_SUFFIX_MAP}`
iterates over the *keys* of `ENTITY_SUFFIX_MAP` (e.g. `"LLC"`, `"INC"`,
...) and uppercases each, producing a **set**. `ENTITY_SUFFIX_MAP.values()`
iterates over the *values* (e.g. `"Limited Liability Company"`), and that
set is uppercased too. The `|` operator is **set union** — combining both
sets while automatically removing duplicates. `sorted(..., key=lambda p:
-len(p.split()))` then sorts the combined set so that phrases with *more
words* come first (the negative sign reverses the normal ascending sort —
see Section 14 for `lambda` and `key=`). This ordering matters because when
later code checks "does this name end with a known suffix phrase?", it must
check multi-word suffixes (like `"LIMITED LIABILITY COMPANY"`) before
single-word ones (like `"COMPANY"`), or it would match too early on a
partial phrase.

**Example — generator expression with `next()`**
([sdn_match_v2.py:292-294](sdn_match_v2.py)):

```python
first_alpha = next(
    (i for i, t in enumerate(tokens) if t and re.search(r'[A-Za-z]', t)), None
)
```

**How it's used here:** `(i for i, t in enumerate(tokens) if ...)` is a
generator — it doesn't build a full list of matching indexes, it produces
them one at a time as needed. `next(generator, default)` pulls just the
*first* value out of it (or returns `None`/`default` if the generator
produces nothing), so this finds "the index of the first token that contains
a letter" without scanning the rest of the list once it's found.

---

## 14. `lambda` and the `key=` argument

**Concept:** `lambda params: expression` creates a small, unnamed
("anonymous") function in a single expression — useful when a function is
needed only briefly, often as the `key=` argument to `sorted()`, `.sort()`,
`max()`, `min()`, etc. `key=` tells these functions *what to compare*
instead of comparing the items directly.

**Example** ([sdn_match_v2.py:355](sdn_match_v2.py)):

```python
am[k].sort(key=lambda x: x[1])
```

**How it's used here:** `am[k]` is a list of tuples `(full_word, skip_flag)`.
`.sort(key=lambda x: x[1])` sorts that list by each tuple's *second* element
(`skip_flag`, a 0 or 1) — without this, `.sort()` would try to compare whole
tuples, which works but isn't what's intended here (it would sort by `full_word`
first). The `lambda x: x[1]` is equivalent to defining:

```python
def _by_skip_flag(x):
    return x[1]
am[k].sort(key=_by_skip_flag)
```
— but written inline, since it's only used once.

---

## 15. `defaultdict`

**Concept:** `collections.defaultdict(factory)` is a dict subclass that
**auto-creates** a value (by calling `factory()`) the first time a missing
key is accessed — instead of raising `KeyError`. Common factories are
`list`, `set`, `int`, and `dict`.

**Example** ([sdn_match_v2.py:351-353](sdn_match_v2.py)):

```python
am = defaultdict(list)
for abbrev, full, skip in rows:
    am[abbrev].append((full, int(skip)))
```

**How it's used here:** Without `defaultdict`, the loop would need:

```python
am = {}
for abbrev, full, skip in rows:
    if abbrev not in am:
        am[abbrev] = []
    am[abbrev].append((full, int(skip)))
```

With `defaultdict(list)`, the first time `am[abbrev]` is accessed for a new
`abbrev`, Python automatically creates an empty list for it, so
`.append(...)` always works. This pattern — building up "key → list of
things" mappings — is used dozens of times throughout the file (e.g.
`aka_by_sdn`, `entity_aka_by_sdn`, `lt_word_index`).

Note line 357, `return dict(am)` — this converts the `defaultdict` back to
a plain `dict` before returning it, so that callers don't accidentally
trigger auto-creation of new empty-list entries by checking keys that don't
exist.

---

## 16. Sets and set operators

**Concept:** A `set` is an unordered collection of unique items — Python's
`HashSet<T>`. Sets support mathematical operations: `|` (union), `&`
(intersection), `-` (difference). Membership testing (`x in my_set`) is
`O(1)`, just like dicts.

**Example** (already seen in Section 13, [sdn_match_v2.py:2854-2856](sdn_match_v2.py)):

```python
_ENTITY_SUFFIX_PHRASES = sorted(
    {abbr.upper() for abbr in ENTITY_SUFFIX_MAP}
    | {full.upper() for full in ENTITY_SUFFIX_MAP.values()},
    key=lambda p: -len(p.split())
)
```

**How it's used here:** Two sets (one of suffix abbreviations like `"LLC"`,
one of expansions like `"LIMITED LIABILITY COMPANY"`) are combined with `|`
into a single set with no duplicates, before being sorted into a list. Using
sets here (rather than lists with `+`) automatically handles the case where
an abbreviation and its expansion happen to collide.

---

## 17. Regular expressions (`re` module)

**Concept:** Python's built-in `re` module provides regex support similar to
.NET's `System.Text.RegularExpressions`. `re.compile(pattern)` precompiles a
pattern into a reusable `re.Pattern` object (faster if used repeatedly).
The `r'...'` prefix creates a **raw string**, where backslashes are literal
— essential for regex patterns full of `\s`, `\d`, etc.

**Example** ([sdn_match_v2.py:193-198](sdn_match_v2.py)):

```python
_LINKED_TO_PAT   = re.compile(r'Linked\s+to:\s*(.+?)(?=\s*[;.]|\s*Linked\s+to:|$)',
                               re.IGNORECASE)
_PHONE_FIELD_PAT = re.compile(
    r'(?:Tel(?:ephone)?|Phone|Fax)[.\s:]*([+\d][\d\s\-\(\)\.\/+]{5,})',
    re.IGNORECASE
)
```

**How it's used here:** `_LINKED_TO_PAT` finds text like `"Linked to: Acme
Corp"` inside OFAC's free-text remarks. `\s+` means "one or more whitespace
characters." `(.+?)` is a **non-greedy capture group** — it captures "as
little as possible" up to whatever comes next, which is the **lookahead**
`(?=...)`: "stop just before a semicolon/period, or another 'Linked to:', or
the end of the string (`$`)," without consuming that stop marker. `(?:...)`
is a **non-capturing group** — `(?:Tel(?:ephone)?|Phone|Fax)` matches
`"Tel"`, `"Telephone"`, `"Phone"`, or `"Fax"` without creating a separate
capture for it. `re.IGNORECASE` makes the whole pattern case-insensitive.

A dynamically-built pattern using an f-string-like raw string
([sdn_match_v2.py:275](sdn_match_v2.py)):

```python
return re.compile(rf'[^A-Za-z0-9\s{escaped}]')
```

`rf'...'` combines **raw** (literal backslashes) and **f-string**
(`{escaped}` is substituted) prefixes — useful when building a regex pattern
that includes both literal backslash-escapes (`\s`) and a runtime variable
(`escaped`).

---

## 18. Classes and the `@dataclass` decorator

**Concept:** A **decorator** is a function that wraps another function or
class to add behavior, written with `@decorator_name` directly above the
`def`/`class`. `@dataclass` (from the `dataclasses` module) automatically
generates boilerplate for a class whose main job is to hold data: a
constructor (`__init__`), a readable `__repr__` (string representation), and
an `__eq__` (equality comparison) — all based on the fields you declare.
This is similar to a C# `record` type.

**Example** ([sdn_match_v2.py:364-393](sdn_match_v2.py)):

```python
@dataclass
class InputRecord:
    # Raw
    entity_type:  Optional[str] = None   # Individual | Entity | Unknown
    entity_name:  Optional[str] = None
    external_id:  Optional[str] = None   # Source-system identifier (e.g. ENTITY_NUM)
    first_name:   Optional[str] = None
    middle_name:  Optional[str] = None
    last_name:    Optional[str] = None
    address1:     Optional[str] = None
    ...
    # Normalized
    entity_name_nm:  str = ''
    first_name_nm:   str = ''
    ...
```

**How it's used here:** Each field declaration `name: type = default` becomes
a constructor parameter with that default. So
`InputRecord(first_name='John', last_name='Smith')` creates an object with
`entity_type=None`, `entity_name=None`, ..., `first_name='John'`,
`last_name='Smith'`, ..., `entity_name_nm=''`, etc. — all without writing an
`__init__` method by hand. Fields are accessed with normal dot notation:
`rec.first_name`, `rec.first_name_nm = "JOHN"`.

The companion `SdnAddress` dataclass ([sdn_match_v2.py:549-560](sdn_match_v2.py))
follows the same pattern for SDN address records.

---

## 19. The `with` statement (context managers)

**Concept:** `with expression as name:` is Python's structured way of
guaranteeing cleanup code runs, even if an exception occurs inside the
block — equivalent to C#'s `using (var x = ...) { }`. Objects that support
`with` (like database connections) implement special `__enter__`/`__exit__`
methods that open/close a resource automatically.

**Example** ([sdn_match_v2.py:3913](sdn_match_v2.py)):

```python
with pyodbc.connect(sdn_cs) as sdn_conn:
    setup_sdn_input_table(sdn_conn, args.sdn_schema, drop=args.drop_sdn_input)
    abbrev_map = load_abbrev_map(sdn_conn)
    name_idx = load_sdn_names(sdn_conn, strip_pat)
    ...
```

**How it's used here:** `pyodbc.connect(sdn_cs)` opens a database connection.
Everything indented under the `with` runs while that connection is open.
When the block ends — whether normally or due to an error — the connection
is automatically closed. This is why the script doesn't have explicit
`conn.close()` calls scattered around.

---

## 20. `enumerate()` and `zip()`

**Concept:** `enumerate(iterable, start=0)` pairs each item with a running
index, producing `(0, item0), (1, item1), ...` (or starting from a different
number if `start` is given). `zip(a, b)` pairs up corresponding items from
two (or more) iterables: `(a[0], b[0]), (a[1], b[1]), ...`, stopping when the
shortest input runs out.

**Example** ([sdn_match_v2.py:4173](sdn_match_v2.py), referenced from
`CODE_FLOW.md`):

```python
for input_record_id, rec in enumerate(input_records, 1):
```

**How it's used here:** `enumerate(input_records, 1)` produces `(1, rec1),
(2, rec2), ...` — a 1-based counter alongside each input record. That
counter (`input_record_id`) becomes the `Input_Record_ID` value written to
every output table, so all the different result tables can be joined back
to the same input record later.

---

## 21. Module-level (global) state and the `global` keyword

**Concept:** Variables defined at the top level of a module are accessible
(read-only) from any function in that module without special syntax. To
**reassign** a module-level variable from inside a function, you must
declare it with `global varname` first — otherwise Python creates a new
*local* variable with the same name instead of modifying the outer one.

**Example** ([sdn_match_v2.py:3344, 3347-3364](sdn_match_v2.py)):

```python
_WK: dict = {}   # populated by _worker_init in each worker process


def _worker_init(sdn_entry_map, name_idx, aka_by_sdn,
                 entity_org_map, entity_aka_norm,
                 jw_name_pct, jw_org_threshold, jw_org_aka_threshold,
                 run_org_word_match, strip_pat):
    """Called once per worker process; stores SDN reference data in _WK."""
    global _WK
    _WK = dict(
        sdn_entry_map        = sdn_entry_map,
        name_idx             = name_idx,
        ...
    )
```

**How it's used here:** `_WK` starts as an empty dict at module load time.
`_worker_init` is run once in each parallel worker process (see Section 24)
and *replaces* `_WK` entirely with a new dict containing that worker's copy
of the SDN reference data. Without `global _WK`, the line `_WK = dict(...)`
would create a local variable `_WK` inside `_worker_init` that disappears
when the function returns — leaving the module-level `_WK` as `{}` forever.
Other functions in the same worker process (`_score_indiv_keys_batch`, etc.)
then read from `_WK` without needing `global`, because they only *read* it,
never reassign it.

`dict(key=value, key2=value2, ...)` here is an alternate way to build a dict
using keyword arguments instead of `{...}` literal syntax — handy when the
keys are fixed identifier names known in advance.

---

## 22. Multi-line expressions and the `+` operator on lists

**Concept:** Parentheses `(...)` let an expression span multiple lines
without needing a line-continuation character. The `+` operator on two lists
concatenates them into a new list (it does **not** add element-by-element,
unlike NumPy arrays).

**Example** ([sdn_match_v2.py:4106-4111](sdn_match_v2.py)):

```python
_futs = (
    [(_pool.submit(_score_indiv_keys_batch,  b), 'indiv')
     for b in indiv_batches]
  + [(_pool.submit(_score_entity_keys_batch, b), 'entity')
     for b in entity_batches]
)
```

**How it's used here:** Two list comprehensions each build a list of
`(future, kind)` tuples — one list for the "individual" batches submitted to
worker processes, one for the "entity" batches. `+` concatenates them into a
single combined list `_futs`, so the code below can loop over *all*
submitted work with one `for` loop, regardless of which kind it was.

---

## 23. Exceptions for control flow vs. validation: `ValueError` and conversions

**Concept:** Functions like `int(...)` and `float(...)` raise `ValueError`
if the input string can't be converted. A common Python pattern is "try the
conversion; if it fails, fall back to a default" — rather than
pre-validating the string format yourself.

**Example** ([sdn_match_v2.py:237-240](sdn_match_v2.py)):

```python
try:
    min_jw_addr = float(addr.get('min_jw_similarity', '0.70'))
except ValueError:
    min_jw_addr = 0.70
```

**How it's used here:** `addr.get('min_jw_similarity', '0.70')` reads a
config value, defaulting to the *string* `'0.70'` if the key is missing.
`float(...)` then converts whatever string was found to a number. If the
config file contains garbage (e.g. `min_jw_similarity = abc`), `float('abc')`
raises `ValueError`, and the code falls back to the numeric default `0.70`
directly — this is repeated for several other config values
(`jw_name_threshold`, `jw_org_threshold`, etc.) at
[sdn_match_v2.py:246-259](sdn_match_v2.py).

---

## 24. Parallelism with `concurrent.futures.ProcessPoolExecutor`

**Concept:** Python has a **Global Interpreter Lock (GIL)** that prevents
multiple threads from executing Python bytecode simultaneously in the same
process — so CPU-heavy work doesn't speed up with threads the way it would
in C#/Java. `ProcessPoolExecutor` instead spreads work across multiple
**separate processes**, each with its own Python interpreter and memory.
`.submit(fn, args)` schedules a function call in a worker process and
immediately returns a `Future` object (a placeholder for "the result, once
it's ready"). `.result()` blocks until that result is available — roughly
analogous to `await someTask` in C#, but synchronous/blocking rather than
`async`.

**Example** ([sdn_match_v2.py:4103-4119](sdn_match_v2.py)):

```python
with ProcessPoolExecutor(max_workers=n_workers,
                         initializer=_worker_init,
                         initargs=_init_args) as _pool:
    _futs = (
        [(_pool.submit(_score_indiv_keys_batch,  b), 'indiv')
         for b in indiv_batches]
      + [(_pool.submit(_score_entity_keys_batch, b), 'entity')
         for b in entity_batches]
    )
    _n_done = 0
    _n_total = n_indiv_keys + n_entity_keys
    for _fut, _kind in _futs:
        _br = _fut.result()
        if _kind == 'indiv':
            indiv_name_cache.update(_br)
        else:
            entity_name_cache.update(_br)
```

**How it's used here:**

- `ProcessPoolExecutor(max_workers=n_workers, initializer=_worker_init,
  initargs=_init_args)` starts `n_workers` separate Python processes. Each
  one immediately calls `_worker_init(*_init_args)` (Section 21) once, to
  load the large, shared SDN reference data a single time per process —
  rather than re-sending it with every individual task (which would be slow,
  since data passed between processes has to be serialized/"pickled").
- `_pool.submit(_score_indiv_keys_batch, b)` hands one batch `b` of unique
  name-keys to a worker process to be scored against all SDN individuals,
  returning a `Future` immediately (without waiting).
- The two list comprehensions submit *all* batches (both "individual" and
  "entity" kinds) up front, tagging each `Future` with a string (`'indiv'`
  or `'entity'`) so the results can be routed to the correct cache
  afterward.
- `for _fut, _kind in _futs:` then unpacks each `(future, kind)` pair (tuple
  unpacking again, Section 5) and calls `.result()`, which blocks until that
  particular batch's worker has finished and returns its dict of results.
  `dict.update(other_dict)` merges `other_dict`'s key/value pairs into the
  existing dict — here, accumulating each batch's results into the overall
  `indiv_name_cache` or `entity_name_cache`.
- The `with` block (Section 19) ensures all worker processes are properly
  shut down once every `Future` has been resolved.

This is the most "advanced" piece of Python in the file, combining context
managers, comprehensions, tuple unpacking, module-global state shared via an
initializer, and inter-process parallelism into one cohesive pattern for
speeding up the cache-precompute step described in
[CODE_FLOW.md](CODE_FLOW.md), Section 10.

---

## Where to go from here

- [CODE_FLOW.md](CODE_FLOW.md) — walks through the *application's* logic
  step by step, using some of the same code shown here in its execution
  context.
- [HOW_IT_WORKS.md](HOW_IT_WORKS.md) — a non-technical description of what
  the application accomplishes overall.
- The official [Python tutorial](https://docs.python.org/3/tutorial/) covers
  every concept above in more depth, with additional examples not drawn from
  this codebase.
