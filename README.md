# breakdown-book

A zero-dependency command-line breakdown and downtime log for small
factories and workshops. When a machine fails, record when it stopped, when
it was fixed, who fixed it and what it cost. Then the questions that matter
have answers: which machine fails the most, how many production hours have
been lost, and what the repairs have cost.

Single Python file, standard library only (Python 3.9+). Fully offline; all
data lives in one local JSON file you own.

## Why this exists

On Razorpay's public "Fix My Itch" list, one entry asks: "Why do factory
owners wait days for industrial repair technicians?" (page fetched and entry
re-verified on 2026-11-18; https://razorpay.com/m/fix-my-itch/). That list
is curated by Razorpay - it shows the problem was submitted and published,
not how common it is.

This tool cannot make a technician arrive faster. It handles the part a
factory owner *can* control while waiting: a disciplined record of every
breakdown - downtime, cost, technician - so repeat-offender machines are
visible, repair spend is totaled, and there are real numbers in hand when
negotiating a maintenance contract or deciding which machine to service
first.

## What it does

- **Machine registry** - name, location, notes.
- **Breakdown log** - one open breakdown per machine at a time; logging a
  second one while a machine is still down is rejected. `resolve` closes it
  with end time, technician, repair cost and a note.
- **Downtime math** - computed from start/end times to the minute, reported
  in hours.
- **Per-machine report** - failures, currently-open count, total downtime,
  total repair cost, worst downtime first.
- **Per-technician report** - jobs done, machine-downtime covered, total
  billed.
- **CSV export** of the full breakdown history.

## What it does NOT do (limits)

- **It does not find, book, or track technicians.** It records what
  happened, not who is available.
- **Not a CMMS.** No preventive-maintenance schedules, spare-parts
  inventory, work orders, or sensor integration.
- **Downtime is as recorded.** The tool cannot know when a machine actually
  stopped - it trusts the times you enter (end must be after start; that is
  validated).
- **Costs are repair bills you type in** - integer paise internally, no
  currency conversion, no tax handling.
- **Durability:** saves are atomic (write to a temp file, then replace the
  store), and a failed save leaves the previous store intact - that behavior
  is covered by tests. Beyond that there are no guarantees against power
  loss mid-write or two people editing the same file at once.

## Landscape note (checked 2026-11-18)

Dated searches on 2026-11-18 for `machine downtime tracking cli open source`
and `equipment breakdown log software small factory` surfaced server/website
uptime-monitoring CLIs on one side and hosted CMMS / maintenance-management
web platforms on the other. This project is instead a single-file offline
CLI for a small factory's own breakdown record. That is a statement about
what those searches found on that date, not a claim that no similar CLI
exists.

## Install and quick start

Requires Python 3.9+. No dependencies.

```bash
python3 breakdown_book.py --help
```

Walkthrough (each command saves automatically to `--db`, default
`breakdown_book.json`):

```bash
python3 breakdown_book.py add-machine --name "Lathe 1" --location "Bay A"
python3 breakdown_book.py add-machine --name "Drill Press" --location "Bay B"

# machine stops
python3 breakdown_book.py log --machine m1 --start 2026-11-18T09:00 \
  --note "belt slip"
python3 breakdown_book.py open                     # who is down right now

# machine is fixed
python3 breakdown_book.py resolve --breakdown b1 --end 2026-11-18T11:30 \
  --technician Ramesh --cost 1500.00 --note "belt replaced"

python3 breakdown_book.py report                   # per machine, worst first
python3 breakdown_book.py techs                    # per technician
python3 breakdown_book.py summary                  # one line, everything
python3 breakdown_book.py export-csv breakdowns.csv
```

Example `report` output:

```
machine                 fails  open   downtime        cost
Lathe 1                     2     0      3.5 h   Rs  2,000.00
Drill Press                 1     1      0.0 h   Rs      0.00
```

Machines can be referred to by id (`m1`) or by exact name. `--start` and
`--end` accept `YYYY-MM-DDTHH:MM` (or a space instead of `T`); omit them to
use the current time.

## Data and tests

- Store: one JSON file (`--db`, default `breakdown_book.json` next to the
  script), written with LF line endings and atomic replace. A failed
  serialization leaves the previous file untouched (tested).
- Money is stored as integer paise (no float drift); amounts with more than
  two decimal places are rejected.
- Tests: 29 unit tests (`python3 -m unittest -v`) covering datetime and
  money parsing, breakdown log/resolve rules (open-breakdown blocking,
  double-resolve and end-before-start rejection), downtime math, per-machine
  and per-technician reports, CSV export, persistence, and the CLI. CI runs
  them on ubuntu-24.04 and windows-latest, Python 3.9 and 3.13.

## License

MIT - see [LICENSE](LICENSE). Copyright (c) 2026 Mehul Jhabak.
