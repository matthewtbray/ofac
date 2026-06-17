# What This Application Does

This document explains, in plain language and in the order it happens, what
the SDN matching tool does. It assumes no programming background. For a
technical, code-level walkthrough, see [CODE_FLOW.md](CODE_FLOW.md). For
deployment details, see [AZURE_DEPLOYMENT.md](AZURE_DEPLOYMENT.md).

## The basic idea

The U.S. Treasury's Office of Foreign Assets Control (OFAC) maintains the
"SDN List" — a list of individuals, companies, and other entities that are
subject to sanctions (people and organizations the government says must not
be done business with). The job of this application is to take a list of
"input records" — people or companies that someone wants to screen — and
check each one against the SDN list, looking for possible matches.

A match isn't necessarily an exact match. Names get misspelled, abbreviated,
translated, or formatted differently between systems. So the tool looks for
both exact matches and close/fuzzy matches, and records how close each
potential match is, so a human reviewer can decide whether it's a real hit
or a false alarm.

## Step by step

### 1. Read settings and starting options

The tool starts by reading a configuration file and a set of command-line
options that control how it runs — for example, which database to read the
input records from, how strict the matching should be, and where to write
the results.

### 2. Load the official SDN sanctions list into memory

The tool reads the entire current SDN list from the database, including:

- Each sanctioned individual or entity's primary name
- All known **alternate names** (also known as "AKAs" — aliases, alternate
  spellings, translations)
- All known **addresses** for each sanctioned party
- Free-text **remarks**, which often contain phrases like "Linked to: [Other
  Company Name]" connecting one sanctioned entity to another
- Any **phone numbers** listed in those remarks

All of this is loaded once, up front, and kept in memory for the entire run
— the tool doesn't go back to the SDN list database over and over.

### 3. Load the list of records to be screened

Next, the tool loads the records that need to be checked against the SDN
list. Depending on how it's configured, this list can come from:

- A CSV file
- A database table
- A list of company "principals" (e.g., officers/owners of a business)
- A "screening input" table set up specifically for this purpose

Each record describes either an **individual** (with a first/last name) or
an **entity** (a company or organization name), along with whatever address
and phone number information is available.

### 4. Clean up and standardize all the names and addresses

Before any comparisons happen, both the SDN data and the input records go
through the same "cleanup" process so that minor formatting differences
don't cause real matches to be missed. This includes:

- Converting everything to uppercase and removing stray punctuation
- Expanding common address abbreviations (e.g., "ST" → "STREET",
  "AVE" → "AVENUE")
- Expanding common business-entity abbreviations (e.g., "LLC" → "LIMITED
  LIABILITY COMPANY", "CORP" → "CORPORATION") so that "Acme LLC" and "Acme
  Limited Liability Company" are recognized as referring to the same kind of
  entity
- Expanding two-letter state and country codes to their full names
- A small phonetic adjustment so that names that sound alike but are spelled
  slightly differently (e.g., "Phillips" vs. "Fillips") are still compared
  fairly

### 5. (Optional) Add test records

If requested, the tool can generate a batch of synthetic "dummy" test
records. These are built by taking real names and addresses from the SDN
list and slightly altering them — close enough that they *should* show up
as a near-match, but not identical. This is purely a testing aid, used to
confirm the matching logic is working as expected; it's not used in normal
production runs.

### 6. Compare every input record against the SDN list

This is the core of the application. For each input record, the tool runs
several different kinds of comparisons ("passes"). Each pass produces a
**similarity score** for every plausible candidate match — not a simple
yes/no, but a percentage indicating how alike two names (or addresses, or
phone numbers) are.

The comparison passes are:

- **Individual name matching** — compares an input person's first and last
  name against the primary name of every sanctioned individual.
- **Individual alias (AKA) matching** — compares the input person's name
  against every known alias of every sanctioned individual.
- **Entity name matching** — compares an input company/organization's name
  against the primary name of every sanctioned entity.
- **Entity alias (AKA) matching** — compares the input entity's name against
  every known alias of every sanctioned entity.
- **"Linked to" matching** — compares the input name against names that
  appear in OFAC's remarks as being "linked to" a sanctioned party (a common
  way OFAC documents related shell companies, family members, business
  partners, etc.).
- **Phone number matching** — compares any phone number on the input record
  against phone numbers found in OFAC's remarks.
- **Address matching** — for any input record that produced at least one
  match above, the tool separately compares its street address, city,
  state, postal code, and country against the addresses on file for the
  matched sanctioned party. (Address matching is skipped for records that
  had no name-based match at all, since there's nothing to compare the
  address *to*.)

For efficiency, the tool doesn't naively compare every single input record
against every single SDN entry one pair at a time. Instead, it first figures
out the unique set of names that actually need to be checked, scores each
of those unique names against the SDN list once, and then reuses those
results for every input record that shares that name. Names are also
"pre-filtered" by shared words and phonetic codes so that, for example, a
name starting with "J" isn't compared against every name starting with "Z."

### 7. Save the results to the database as it goes

Rather than holding every result in memory until the very end (which could
use a huge amount of memory for large input files), the tool periodically
writes its results out to the database in batches as it works through the
input records — by default, every 50 input records.

If an input record produces **no matches at all** across every comparison
type above, that fact is also recorded, so reviewers can see that the record
was checked and came back clean.

### 8. Build summary tables

After all the detailed comparison results have been written, the tool builds
two **summary tables** — one for individuals and one for entities/
organizations. For each input record and each sanctioned party it matched
against (even loosely), the summary table pulls together a single row
showing:

- The input record's name, city, and country
- The matched SDN party's name, city, and country
- Which types of matches were found (name, alias, address, phone,
  "linked to," etc.) and how strong each match was
- Whether the city/country on the input record lines up with the city/
  country on file for the matched SDN party

This summary view is intended to be the primary thing a human reviewer looks
at — rather than digging through every individual comparison-pass table, the
summary brings the key facts about each potential match into one row.

### 9. Print a final report

When the run finishes, the tool prints a short summary to the screen showing:

- How many input records were processed
- How many rows were written to each results table
- How many rows ended up in each summary table

This gives an immediate sanity check that the run completed and produced a
reasonable amount of output.

## What you, as a reviewer, ultimately work with

In day-to-day use, the two **summary tables** (individuals and
entities/organizations) are the starting point. Each row represents one
"input record vs. one SDN-listed party" pairing that had at least some
similarity worth looking at. From there, a reviewer can:

- See at a glance how strong the name/address/phone matches were
- Decide whether a row is a true potential hit (needs further investigation)
  or a false positive (coincidental similarity that can be dismissed)
- Drill into the more detailed comparison-pass tables if more context is
  needed about *why* a particular score was assigned
