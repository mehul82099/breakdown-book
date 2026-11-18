#!/usr/bin/env python3
"""breakdown-book: zero-dependency machine breakdown and downtime log CLI for
small factories and workshops.

When a machine fails, record when it stopped, when it was fixed, who fixed it
and what it cost. Then answer the questions that matter when a technician is
days away: which machine fails the most, how many production hours have been
lost, and what the repairs have cost.

Python 3.9+, standard library only. One JSON file, offline, no accounts.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import tempfile
from datetime import datetime

DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "breakdown_book.json")

DT_FORMATS = ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M")


class BreakdownBookError(Exception):
    """User-facing error (bad input, unknown id, ...)."""


def now_iso():
    return datetime.now().replace(second=0, microsecond=0).isoformat()


def parse_dt(text):
    for fmt in DT_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    raise BreakdownBookError(
        "bad datetime %r (expected YYYY-MM-DDTHH:MM)" % text)


def parse_amount(text):
    """Rupees string -> integer paise; max 2 decimals, must be >= 0."""
    text = (text or "").strip()
    if not re.fullmatch(r"\d+(\.\d{1,2})?", text):
        raise BreakdownBookError(
            "bad amount %r (e.g. 1250 or 1250.50, paise beyond 2 decimals "
            "not accepted)" % text)
    rupees, _, frac = text.partition(".")
    frac = (frac + "00")[:2]
    return int(rupees) * 100 + int(frac)


def format_paise(paise):
    sign = "-" if paise < 0 else ""
    paise = abs(paise)
    s = str(paise // 100)
    # Indian digit grouping
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts + [tail])
    return "%s%s.%02d" % (sign, s, paise % 100)


def minutes_between(start_iso, end_iso):
    delta = datetime.fromisoformat(end_iso) - datetime.fromisoformat(start_iso)
    return int(delta.total_seconds() // 60)


def fmt_hours(minutes):
    return "%.1f" % (minutes / 60.0)


class BreakdownBook:
    def __init__(self):
        self.state = {"machines": [], "breakdowns": [],
                      "next_machine_id": 1, "next_breakdown_id": 1}

    # -- persistence ------------------------------------------------------

    @classmethod
    def load(cls, path):
        if not path.exists():
            return cls()
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise BreakdownBookError(
                "could not parse %s: %s" % (path, exc))
        book = cls()
        for key in state:
            if key in book.state:
                book.state[key] = state[key]
        return book

    def save(self, path):
        data = json.dumps(self.state, indent=2, ensure_ascii=False) + "\n"
        fd, tmp_name = tempfile.mkstemp(
            prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
                fh.write(data)
            os.replace(tmp_name, path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    # -- machines ---------------------------------------------------------

    def add_machine(self, name, location="", notes=""):
        name = (name or "").strip()
        if not name:
            raise BreakdownBookError("machine name must not be empty")
        if any(m["name"].lower() == name.lower()
               for m in self.state["machines"]):
            raise BreakdownBookError("machine already exists: %s" % name)
        machine = {"id": "m%d" % self.state["next_machine_id"], "name": name,
                   "location": (location or "").strip(),
                   "notes": (notes or "").strip(), "added": now_iso()[:10]}
        self.state["next_machine_id"] += 1
        self.state["machines"].append(machine)
        return machine

    def get_machine(self, ref):
        ref = (ref or "").strip()
        for m in self.state["machines"]:
            if m["id"] == ref or m["name"].lower() == ref.lower():
                return m
        raise BreakdownBookError("no such machine: %s" % ref)

    # -- breakdowns -------------------------------------------------------

    def log_breakdown(self, machine_ref, start, note=""):
        machine = self.get_machine(machine_ref)
        open_ = [b for b in self.state["breakdowns"]
                 if b["machine_id"] == machine["id"] and b["end"] is None]
        if open_:
            raise BreakdownBookError(
                "%s already has an open breakdown (%s from %s) - resolve it "
                "first" % (machine["name"], open_[0]["id"], open_[0]["start"]))
        bd = {"id": "b%d" % self.state["next_breakdown_id"],
              "machine_id": machine["id"],
              "start": start.replace(second=0, microsecond=0).isoformat(),
              "end": None, "technician": "", "cost_paise": 0,
              "note": (note or "").strip()}
        self.state["next_breakdown_id"] += 1
        self.state["breakdowns"].append(bd)
        return bd

    def get_breakdown(self, ref):
        ref = (ref or "").strip()
        for b in self.state["breakdowns"]:
            if b["id"] == ref:
                return b
        raise BreakdownBookError("no such breakdown: %s" % ref)

    def resolve(self, breakdown_ref, end, technician="", cost_paise=0,
                note=""):
        bd = self.get_breakdown(breakdown_ref)
        if bd["end"] is not None:
            raise BreakdownBookError(
                "%s is already resolved (ended %s)" % (bd["id"], bd["end"]))
        if end <= datetime.fromisoformat(bd["start"]):
            raise BreakdownBookError(
                "end time must be after the start time (%s)" % bd["start"])
        bd["end"] = end.replace(second=0, microsecond=0).isoformat()
        bd["technician"] = (technician or "").strip()
        bd["cost_paise"] = cost_paise
        if note:
            bd["note"] = (bd["note"] + " | " if bd["note"] else "") \
                + note.strip()
        return bd

    def open_breakdowns(self):
        return [b for b in self.state["breakdowns"] if b["end"] is None]

    # -- reports ----------------------------------------------------------

    def machine_report(self):
        """Per-machine totals, worst downtime first."""
        rows = []
        for m in self.state["machines"]:
            bds = [b for b in self.state["breakdowns"]
                   if b["machine_id"] == m["id"]]
            closed = [b for b in bds if b["end"] is not None]
            downtime = sum(minutes_between(b["start"], b["end"])
                           for b in closed)
            cost = sum(b["cost_paise"] for b in closed)
            rows.append({"machine": m, "count": len(bds),
                         "open": len(bds) - len(closed),
                         "downtime_min": downtime, "cost_paise": cost})
        rows.sort(key=lambda r: (-r["downtime_min"], -r["cost_paise"],
                                 r["machine"]["name"]))
        return rows

    def technician_report(self):
        techs = {}
        for b in self.state["breakdowns"]:
            if b["end"] is None or not b["technician"]:
                continue
            t = techs.setdefault(b["technician"], {"jobs": 0,
                                                   "cost_paise": 0,
                                                   "downtime_min": 0})
            t["jobs"] += 1
            t["cost_paise"] += b["cost_paise"]
            t["downtime_min"] += minutes_between(b["start"], b["end"])
        return sorted(techs.items(), key=lambda kv: (-kv[1]["jobs"],
                                                     kv[0]))

    def export_csv(self, out_path):
        with open(out_path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(["breakdown_id", "machine", "start", "end",
                        "downtime_hours", "technician", "cost", "note"])
            for b in self.state["breakdowns"]:
                m = self.get_machine(b["machine_id"])
                downtime = (fmt_hours(minutes_between(b["start"], b["end"]))
                            if b["end"] else "")
                w.writerow([b["id"], m["name"], b["start"], b["end"] or "",
                            downtime, b["technician"],
                            format_paise(b["cost_paise"]), b["note"]])

    def summary(self):
        closed = [b for b in self.state["breakdowns"] if b["end"] is not None]
        return {"machines": len(self.state["machines"]),
                "breakdowns": len(self.state["breakdowns"]),
                "open": len(self.open_breakdowns()),
                "downtime_min": sum(minutes_between(b["start"], b["end"])
                                    for b in closed),
                "cost_paise": sum(b["cost_paise"] for b in closed)}


# -- CLI --------------------------------------------------------------------

def cmd_add_machine(book, args):
    m = book.add_machine(args.name, args.location, args.notes)
    print("added machine %s: %s" % (m["id"], m["name"]))


def cmd_machines(book, args):
    if not book.state["machines"]:
        print("no machines yet")
        return
    for m in book.state["machines"]:
        loc = ("  (%s)" % m["location"]) if m["location"] else ""
        print("%-4s  %s%s" % (m["id"], m["name"], loc))


def cmd_log(book, args):
    bd = book.log_breakdown(args.machine,
                            parse_dt(args.start) if args.start
                            else datetime.now().replace(second=0,
                                                        microsecond=0),
                            args.note)
    m = book.get_machine(args.machine)
    print("logged %s: %s down since %s" % (bd["id"], m["name"], bd["start"]))


def cmd_resolve(book, args):
    bd = book.resolve(args.breakdown,
                      parse_dt(args.end) if args.end
                      else datetime.now().replace(second=0, microsecond=0),
                      args.technician,
                      parse_amount(args.cost) if args.cost else 0,
                      args.note)
    mins = minutes_between(bd["start"], bd["end"])
    print("resolved %s: %s h downtime, cost Rs %s%s"
          % (bd["id"], fmt_hours(mins), format_paise(bd["cost_paise"]),
             ("  (%s)" % bd["technician"]) if bd["technician"] else ""))


def cmd_open(book, args):
    open_ = book.open_breakdowns()
    if not open_:
        print("no open breakdowns")
        return
    for b in open_:
        m = book.get_machine(b["machine_id"])
        print("%-4s  %-22s  down since %s%s"
              % (b["id"], m["name"], b["start"],
                 ("  - %s" % b["note"]) if b["note"] else ""))


def cmd_report(book, args):
    rows = book.machine_report()
    if not rows:
        print("no machines yet")
        return
    print("%-22s  %5s  %4s  %9s  %10s"
          % ("machine", "fails", "open", "downtime", "cost"))
    for r in rows:
        print("%-22s  %5d  %4d  %7s h  Rs %9s"
              % (r["machine"]["name"], r["count"], r["open"],
                 fmt_hours(r["downtime_min"]),
                 format_paise(r["cost_paise"])))


def cmd_techs(book, args):
    rows = book.technician_report()
    if not rows:
        print("no completed repairs with a technician yet")
        return
    for name, t in rows:
        print("%-22s  %d job(s), %s h machine-downtime, Rs %s billed"
              % (name, t["jobs"], fmt_hours(t["downtime_min"]),
                 format_paise(t["cost_paise"])))


def cmd_export_csv(book, args):
    book.export_csv(args.path)
    print("wrote %s" % args.path)


def cmd_summary(book, args):
    s = book.summary()
    print("machines: %d   breakdowns: %d (%d open)   downtime: %s h   "
          "repair cost: Rs %s"
          % (s["machines"], s["breakdowns"], s["open"],
             fmt_hours(s["downtime_min"]), format_paise(s["cost_paise"])))


def build_parser():
    p = argparse.ArgumentParser(
        prog="breakdown-book",
        description="Machine breakdown, downtime and repair-cost log for "
                    "small factories. Offline, one JSON file.")
    p.add_argument("--db", default=DEFAULT_DB,
                   help="store file (default: breakdown_book.json next to "
                        "this script)")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("add-machine", help="register a machine")
    sp.add_argument("--name", required=True)
    sp.add_argument("--location", default="")
    sp.add_argument("--notes", default="")
    sp.set_defaults(func=cmd_add_machine)

    sp = sub.add_parser("machines", help="list machines")
    sp.set_defaults(func=cmd_machines)

    sp = sub.add_parser("log", help="log a new breakdown")
    sp.add_argument("--machine", required=True, help="id or exact name")
    sp.add_argument("--start", default=None,
                    help="YYYY-MM-DDTHH:MM (default: now)")
    sp.add_argument("--note", default="")
    sp.set_defaults(func=cmd_log)

    sp = sub.add_parser("resolve", help="close an open breakdown")
    sp.add_argument("--breakdown", required=True, help="breakdown id")
    sp.add_argument("--end", default=None,
                    help="YYYY-MM-DDTHH:MM (default: now)")
    sp.add_argument("--technician", default="")
    sp.add_argument("--cost", default=None, help="repair cost in rupees")
    sp.add_argument("--note", default="")
    sp.set_defaults(func=cmd_resolve)

    sp = sub.add_parser("open", help="list machines currently down")
    sp.set_defaults(func=cmd_open)

    sp = sub.add_parser("report", help="per-machine failures/downtime/cost")
    sp.set_defaults(func=cmd_report)

    sp = sub.add_parser("techs", help="per-technician jobs and billing")
    sp.set_defaults(func=cmd_techs)

    sp = sub.add_parser("export-csv", help="export all breakdowns to CSV")
    sp.add_argument("path")
    sp.set_defaults(func=cmd_export_csv)

    sp = sub.add_parser("summary", help="one-line store summary")
    sp.set_defaults(func=cmd_summary)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    from pathlib import Path
    db = Path(args.db)
    try:
        book = BreakdownBook.load(db)
        args.func(book, args)
        book.save(db)
    except BreakdownBookError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
