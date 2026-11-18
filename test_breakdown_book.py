#!/usr/bin/env python3
"""Tests for breakdown_book. Run: python -m unittest -v"""

import csv
import io
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime
from pathlib import Path

import breakdown_book as bb

DT = datetime  # shorthand


class ParseTests(unittest.TestCase):
    def test_parse_dt_both_formats(self):
        self.assertEqual(bb.parse_dt("2026-11-18T09:30"), DT(2026, 11, 18, 9, 30))
        self.assertEqual(bb.parse_dt("2026-11-18 09:30"), DT(2026, 11, 18, 9, 30))

    def test_parse_dt_rejects(self):
        for bad in ("18/11/2026 09:30", "2026-11-18", "9am", ""):
            with self.assertRaises(bb.BreakdownBookError):
                bb.parse_dt(bad)

    def test_parse_amount(self):
        self.assertEqual(bb.parse_amount("1250"), 125000)
        self.assertEqual(bb.parse_amount("1250.50"), 125050)
        self.assertEqual(bb.parse_amount("0"), 0)

    def test_parse_amount_rejects(self):
        for bad in ("10.005", "-5", "abc", "1,250"):
            with self.assertRaises(bb.BreakdownBookError):
                bb.parse_amount(bad)

    def test_format_indian_grouping(self):
        self.assertEqual(bb.format_paise(12345678), "1,23,456.78")
        self.assertEqual(bb.format_paise(100), "1.00")

    def test_minutes_between(self):
        self.assertEqual(bb.minutes_between("2026-11-18T09:00",
                                            "2026-11-18T11:30:00"), 150)


class BookFixture(unittest.TestCase):
    def setUp(self):
        self.book = bb.BreakdownBook()
        self.book.add_machine("Lathe 1", location="Bay A")
        self.book.add_machine("Drill Press", location="Bay B")


class MachineTests(BookFixture):
    def test_add_and_lookup(self):
        m = self.book.get_machine("lathe 1")
        self.assertEqual(m["id"], "m1")

    def test_duplicate_rejected(self):
        with self.assertRaises(bb.BreakdownBookError):
            self.book.add_machine("LATHE 1")

    def test_empty_name_rejected(self):
        with self.assertRaises(bb.BreakdownBookError):
            self.book.add_machine("  ")

    def test_unknown_machine(self):
        with self.assertRaises(bb.BreakdownBookError):
            self.book.get_machine("m99")


class BreakdownTests(BookFixture):
    def test_log_and_resolve(self):
        bd = self.book.log_breakdown("m1", DT(2026, 11, 18, 9, 0), "belt slip")
        self.assertEqual(bd["id"], "b1")
        self.assertIsNone(bd["end"])
        out = self.book.resolve("b1", DT(2026, 11, 18, 11, 30),
                                technician="Ramesh", cost_paise=150000,
                                note="belt replaced")
        self.assertEqual(out["end"], "2026-11-18T11:30:00")
        self.assertEqual(out["technician"], "Ramesh")
        self.assertEqual(out["cost_paise"], 150000)
        self.assertIn("belt slip", out["note"])
        self.assertIn("belt replaced", out["note"])

    def test_resolve_by_machine_open_order(self):
        self.book.log_breakdown("m1", DT(2026, 11, 18, 9, 0))
        self.book.resolve("b1", DT(2026, 11, 18, 10, 0))
        bd2 = self.book.log_breakdown("m1", DT(2026, 11, 18, 12, 0))
        self.assertEqual(bd2["id"], "b2")

    def test_open_breakdown_blocks_new_log(self):
        self.book.log_breakdown("m1", DT(2026, 11, 18, 9, 0))
        with self.assertRaises(bb.BreakdownBookError):
            self.book.log_breakdown("m1", DT(2026, 11, 18, 10, 0))
        # different machine is fine
        self.book.log_breakdown("m2", DT(2026, 11, 18, 10, 0))

    def test_double_resolve_rejected(self):
        self.book.log_breakdown("m1", DT(2026, 11, 18, 9, 0))
        self.book.resolve("b1", DT(2026, 11, 18, 10, 0))
        with self.assertRaises(bb.BreakdownBookError):
            self.book.resolve("b1", DT(2026, 11, 18, 11, 0))

    def test_end_before_start_rejected(self):
        self.book.log_breakdown("m1", DT(2026, 11, 18, 9, 0))
        with self.assertRaises(bb.BreakdownBookError):
            self.book.resolve("b1", DT(2026, 11, 18, 9, 0))
        with self.assertRaises(bb.BreakdownBookError):
            self.book.resolve("b1", DT(2026, 11, 18, 8, 0))

    def test_unknown_breakdown(self):
        with self.assertRaises(bb.BreakdownBookError):
            self.book.resolve("b99", DT(2026, 11, 18, 10, 0))

    def test_open_listing(self):
        self.book.log_breakdown("m1", DT(2026, 11, 18, 9, 0))
        self.assertEqual(len(self.book.open_breakdowns()), 1)
        self.book.resolve("b1", DT(2026, 11, 18, 10, 0))
        self.assertEqual(self.book.open_breakdowns(), [])


class ReportFixture(BookFixture):
    def setUp(self):
        super().setUp()
        # Lathe 1: two breakdowns, 2.5 h + 1 h, Rs 1500 + Rs 500
        self.book.log_breakdown("m1", DT(2026, 11, 10, 9, 0))
        self.book.resolve("b1", DT(2026, 11, 10, 11, 30),
                          technician="Ramesh", cost_paise=150000)
        self.book.log_breakdown("m1", DT(2026, 11, 12, 14, 0))
        self.book.resolve("b2", DT(2026, 11, 12, 15, 0),
                          technician="Suresh", cost_paise=50000)
        # Drill Press: one open breakdown
        self.book.log_breakdown("m2", DT(2026, 11, 18, 8, 0), "spindle noise")


class ReportTests(ReportFixture):
    def test_machine_report(self):
        rows = self.book.machine_report()
        lathe = [r for r in rows if r["machine"]["id"] == "m1"][0]
        drill = [r for r in rows if r["machine"]["id"] == "m2"][0]
        self.assertEqual(lathe["count"], 2)
        self.assertEqual(lathe["open"], 0)
        self.assertEqual(lathe["downtime_min"], 210)  # 150 + 60
        self.assertEqual(lathe["cost_paise"], 200000)
        self.assertEqual(drill["open"], 1)
        self.assertEqual(drill["downtime_min"], 0)  # open: no downtime yet
        # worst downtime first
        self.assertEqual(rows[0]["machine"]["id"], "m1")

    def test_technician_report(self):
        rows = dict(self.book.technician_report())
        self.assertEqual(rows["Ramesh"]["jobs"], 1)
        self.assertEqual(rows["Ramesh"]["cost_paise"], 150000)
        self.assertEqual(rows["Ramesh"]["downtime_min"], 150)
        self.assertEqual(rows["Suresh"]["jobs"], 1)

    def test_summary(self):
        s = self.book.summary()
        self.assertEqual(s["machines"], 2)
        self.assertEqual(s["breakdowns"], 3)
        self.assertEqual(s["open"], 1)
        self.assertEqual(s["downtime_min"], 210)
        self.assertEqual(s["cost_paise"], 200000)

    def test_export_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "bd.csv"
            self.book.export_csv(out)
            with open(out, newline="", encoding="utf-8") as f:
                rows = list(csv.reader(f))
        self.assertEqual(rows[0], ["breakdown_id", "machine", "start", "end",
                                   "downtime_hours", "technician", "cost",
                                   "note"])
        self.assertEqual(len(rows), 4)
        b1 = rows[1]
        self.assertEqual(b1[0], "b1")
        self.assertEqual(b1[4], "2.5")
        self.assertEqual(b1[5], "Ramesh")
        self.assertEqual(b1[6], "1,500.00")
        open_row = [r for r in rows if r[0] == "b3"][0]
        self.assertEqual(open_row[3], "")
        self.assertEqual(open_row[4], "")


class PersistenceTests(ReportFixture):
    def test_round_trip_and_lf_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "book.json"
            self.book.save(db)
            self.assertNotIn(b"\r\n", db.read_bytes())
            loaded = bb.BreakdownBook.load(db)
        self.assertEqual(loaded.summary(), self.book.summary())
        rows = loaded.machine_report()
        self.assertEqual(rows[0]["downtime_min"], 210)

    def test_failed_save_leaves_original_intact(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "book.json"
            self.book.save(db)
            before = db.read_bytes()
            self.book.state["broken"] = object()
            try:
                with self.assertRaises(TypeError):
                    self.book.save(db)
                self.assertEqual(db.read_bytes(), before)
                leftovers = [p for p in Path(tmp).iterdir()
                             if p.name != "book.json"]
                self.assertEqual(leftovers, [])
            finally:
                del self.book.state["broken"]

    def test_corrupt_file_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "bad.json"
            db.write_text("{oops", encoding="utf-8")
            with self.assertRaises(bb.BreakdownBookError):
                bb.BreakdownBook.load(db)

    def test_missing_file_gives_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            loaded = bb.BreakdownBook.load(Path(tmp) / "none.json")
        self.assertEqual(loaded.summary()["machines"], 0)


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "cli.json")

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = bb.main(["--db", self.db] + list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_full_flow(self):
        code, out, _ = self.run_cli("add-machine", "--name", "Lathe 1",
                                    "--location", "Bay A")
        self.assertEqual(code, 0)
        self.assertIn("m1", out)
        code, out, _ = self.run_cli("log", "--machine", "m1",
                                    "--start", "2026-11-18T09:00",
                                    "--note", "belt slip")
        self.assertEqual(code, 0)
        self.assertIn("b1", out)
        code, out, _ = self.run_cli("open")
        self.assertIn("Lathe 1", out)
        code, out, _ = self.run_cli("resolve", "--breakdown", "b1",
                                    "--end", "2026-11-18T11:30",
                                    "--technician", "Ramesh",
                                    "--cost", "1500.00")
        self.assertEqual(code, 0)
        self.assertIn("2.5 h", out)
        self.assertIn("1,500.00", out)
        code, out, _ = self.run_cli("report")
        self.assertIn("Lathe 1", out)
        self.assertIn("2.5 h", out)
        code, out, _ = self.run_cli("techs")
        self.assertIn("Ramesh", out)
        code, out, _ = self.run_cli("summary")
        self.assertIn("downtime: 2.5 h", out)
        out_path = str(Path(self.tmp.name) / "bd.csv")
        code, _, _ = self.run_cli("export-csv", out_path)
        self.assertEqual(code, 0)
        with open(out_path, newline="", encoding="utf-8") as f:
            self.assertEqual(len(list(csv.reader(f))), 2)

    def test_machines_command(self):
        self.run_cli("add-machine", "--name", "Lathe 1", "--location", "Bay A")
        code, out, _ = self.run_cli("machines")
        self.assertIn("Bay A", out)

    def test_error_exit_code(self):
        code, _, err = self.run_cli("log", "--machine", "m99",
                                    "--start", "2026-11-18T09:00")
        self.assertEqual(code, 2)
        self.assertIn("no such machine", err)

    def test_resolve_without_end_uses_now(self):
        # start far in the past: "now" must be after it in any CI timezone
        self.run_cli("add-machine", "--name", "Lathe 1")
        self.run_cli("log", "--machine", "m1", "--start", "2020-01-01T09:00")
        code, out, _ = self.run_cli("resolve", "--breakdown", "b1")
        self.assertEqual(code, 0)
        self.assertIn("resolved b1", out)


if __name__ == "__main__":
    unittest.main()
