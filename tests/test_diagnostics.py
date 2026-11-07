"""Game Diagnostics: attempts, what a launch's observations support, crash
correlation, the scan, and the support bundle.

Processes, event logs and manifests are simulated here: a test that mocks a
process does not show a real WoW launch. The real process probes are exercised
against real child processes on the platform running the tests."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from equpdater import clientscan, diagnostics as d, platforms  # noqa: E402
from equpdater.diagnostics import Proc  # noqa: E402

PE_X86 = (b"MZ" + b"\0" * 58 + (64).to_bytes(4, "little")
          + b"PE\0\0" + (0x14c).to_bytes(2, "little") + b"\0" * 64)
PE_X64 = (b"MZ" + b"\0" * 58 + (64).to_bytes(4, "little")
          + b"PE\0\0" + (0x8664).to_bytes(2, "little") + b"\0" * 64)
MPQ_OK = b"MPQ\x1a" + (32).to_bytes(4, "little") + (20).to_bytes(4, "little") + b"\0" * 8


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


class FakeProbe:
    """A process list a test sets by hand."""

    def __init__(self):
        self.procs, self.codes, self.windows = [], {}, {}

    def snapshot(self, names):
        return list(self.procs)

    def hold(self, proc):
        pass

    def exit_code(self, proc):
        return self.codes.get(proc.key)

    def window_texts(self, pid):
        return self.windows.get(pid, [])

    def modules(self, pid):
        return []


class FakeProc:
    """What Popen gives: a pid and poll()."""

    def __init__(self, pid=500, code=None):
        self.pid, self.code = pid, code

    def poll(self):
        return self.code


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="equ-diag-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.client = os.path.join(self.tmp, "OctoWoW")
        os.makedirs(self.client)
        self.store = d.Store(os.path.join(self.tmp, "data"))
        self.mono, self.wall = Clock(), Clock(time.time())
        self.probe = FakeProbe()

    def attempt(self, diagnostic=False):
        return d.Attempt(self.store, self.client, "2.1.3", "win32", diagnostic,
                         clock=self.mono, wall=self.wall)

    def game(self, pid=900, start=5.0, client=None):
        path = os.path.join(client or self.client, "WoW.exe")
        return Proc(pid, "WoW.exe", start, path, [path], None, 500)

    def tick(self, seconds):
        self.mono.t += seconds
        self.wall.t += seconds

    def write(self, rel, data=b"x"):
        path = os.path.join(self.client, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def observer(self, attempt, proc=None, role="launcher", name="VanillaFixes.exe",
                 **kw):
        attempt.data["route"] = {"runner": "windows", "target": name}
        return d.Observer(attempt, self.probe, proc or FakeProc(), role, name, **kw)

    def outcome(self, attempt):
        return d.classify(attempt.data)


class TestRecord(Base):
    def test_on_disk_before_anything_is_checked(self):
        a = self.attempt()
        back = self.store.load(a.id)
        self.assertEqual(back["id"], a.id)
        self.assertEqual(back["events"][0]["kind"], "play_clicked")

    def test_two_attempts_do_not_share_anything(self):
        a, b = self.attempt(), self.attempt()
        self.assertNotEqual(a.id, b.id)
        self.assertNotEqual(a.output_path(), b.output_path())

    def test_write_failure_does_not_stop_the_launch(self):
        a = self.attempt()
        with mock.patch("os.replace", side_effect=OSError(28, "No space left")):
            a.event("spawned", pid=1)
        self.assertIn("No space left", self.store.last_error)
        self.assertTrue(a.has("spawned"))

    def test_interrupted_write_leaves_the_last_good_record(self):
        a = self.attempt()
        with mock.patch("json.dump", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                a.event("spawned", pid=1)
        self.assertEqual(self.store.load(a.id)["id"], a.id)

    def test_damaged_record_still_lists(self):
        a = self.attempt()
        with open(os.path.join(a.folder(), "attempt.json"), "w") as f:
            f.write('{"id": "half')
        data = self.store.load(a.id)
        self.assertEqual(data["outcome"], "unavailable")
        self.assertTrue(data["damaged"])
        self.assertEqual(d.classify(data)[0], "unavailable")

    def test_prune_keeps_a_bounded_number(self):
        keep = None
        for i in range(d.MAX_ATTEMPTS + 5):
            self.wall.t += 2
            keep = self.attempt()
        self.store.prune(keep=keep.id)
        self.assertLessEqual(len(self.store.ids()), d.MAX_ATTEMPTS)
        self.assertIn(keep.id, self.store.ids())


class TestBlocked(Base):
    def outcome_of(self, *args, **kw):
        a = self.attempt()
        a.block(*args, **kw)
        return self.store.load(a.id)

    def test_each_stops_with_its_own_reason(self):
        for stage, detail, err in (
                ("executable", "The launch target WoW.exe was not found", None),
                ("launch_settings", "runner_missing: umu-run", None),
                ("process_creation", "VanillaFixes.exe could not be started",
                 OSError(13, "Permission denied"))):
            data = self.outcome_of(stage, detail, err)
            self.assertEqual(data["outcome"], "blocked")
            self.assertIn(stage, data["explanation"])
            self.assertIn(detail, data["explanation"])
            self.assertTrue(data["finished"])

    def test_windows_error_code_is_kept(self):
        err = OSError(22, "%1 is not a valid Win32 application")
        err.winerror = 193
        data = self.outcome_of("process_creation", "could not be started", err)
        self.assertEqual(data["blocked"]["winerror"], 193)
        self.assertIn("193", data["explanation"])

    def test_preflight_finds_what_cannot_start(self):
        missing_cwd = type("Plan", (), {"cwd": os.path.join(self.tmp, "nope"),
                                        "argv": ["wine", "{exe}"]})
        exe = self.write("WoW.exe", b"not a program")
        a = self.attempt()
        d.preflight(a, self.client, exe, missing_cwd)
        checks = {p["check"]: p for p in a.data["preflight"]}
        self.assertFalse(checks["executable"]["ok"])
        self.assertFalse(checks["working_folder"]["ok"])
        self.assertFalse(checks["arguments"]["ok"])
        self.assertIn("{exe}", checks["arguments"]["detail"])

    def test_preflight_unreadable_exe(self):
        plan = type("Plan", (), {"cwd": self.client, "argv": []})
        a = self.attempt()
        with mock.patch.object(d, "pe_machine", side_effect=PermissionError(13, "denied")):
            d.preflight(a, self.client, os.path.join(self.client, "WoW.exe"), plan)
        check = next(p for p in a.data["preflight"] if p["check"] == "executable")
        self.assertFalse(check["ok"])
        self.assertIn("cannot be read", check["detail"])
        self.assertEqual(check["code"], 13)

    def test_preflight_missing_folder(self):
        plan = type("Plan", (), {"cwd": "", "argv": []})
        a = self.attempt()
        d.preflight(a, os.path.join(self.tmp, "gone"), "x", plan)
        self.assertFalse(a.data["preflight"][0]["ok"])

    def test_missing_runner_keeps_its_name(self):
        data = self.outcome_of("launch_settings", "runner_missing: umu-run")
        self.assertIn("umu-run", data["explanation"])


class TestHandoff(Base):
    def test_vanillafixes_exits_and_hands_off(self):
        a = self.attempt()
        proc = FakeProc()
        ob = self.observer(a, proc)
        self.tick(0.8)
        proc.code = 0
        ob.step()
        self.assertEqual(self.outcome(a)[0], "in_progress")
        self.tick(0.6)
        self.probe.procs = [self.game()]
        ob.step()
        outcome, text = self.outcome(a)
        self.assertEqual(outcome, "running")
        self.assertIn("exited normally", text)
        self.assertIn("1.4 seconds", text)
        self.tick(d.CONFIRM_RUNNING_S + 1)
        ob.step()
        self.assertTrue(a.has("game_confirmed"))

    def test_vanillafixes_exits_with_an_error_and_its_output(self):
        output = self.write("output.txt", b"line one\nd3d9.dll could not be loaded\n")
        a = self.attempt()
        proc = FakeProc()
        ob = self.observer(a, proc, output=output)
        self.tick(0.8)
        proc.code = 3
        ob.step()
        outcome, text = self.outcome(a)
        self.assertEqual(outcome, "launcher_error")
        for part in ("VanillaFixes.exe", "code 3", "0.8 seconds", "not observed",
                     "d3d9.dll could not be loaded"):
            self.assertIn(part, text)

    def test_vanillafixes_message_box_is_its_voice(self):
        a = self.attempt()
        proc = FakeProc()
        self.probe.windows[proc.pid] = [
            "VanillaFixes", "VfPatcher.dll not found. Extract all files into the "
                            "game directory."]
        ob = self.observer(a, proc)
        self.tick(1.5)
        ob.step()
        outcome, text = self.outcome(a)
        self.assertEqual(outcome, "launcher_says")
        self.assertIn("VfPatcher.dll not found", text)
        self.tick(1)
        ob.step()
        self.assertEqual(len([e for e in a.data["events"]
                              if e["kind"] == "launcher_message"]), 1)

    def test_a_wrapper_exiting_zero_with_no_game_stays_unverified(self):
        a = self.attempt()
        proc = FakeProc()
        ob = self.observer(a, proc, name="run-wow.sh")
        self.tick(1)
        proc.code = 0
        ob.step()
        self.assertEqual(self.outcome(a)[0], "in_progress")
        self.tick(d.OBSERVE_WINDOW_S)
        ob.step()
        outcome, text = self.outcome(a)
        self.assertEqual(outcome, "no_game")
        self.assertIn("only that", text)
        self.assertNotIn("crash", text.lower())

    def test_slow_start_is_not_a_failure_until_the_window_ends(self):
        a = self.attempt()
        ob = self.observer(a)
        self.tick(d.OBSERVE_WINDOW_S - 5)
        ob.step()
        self.assertEqual(self.outcome(a)[0], "in_progress")
        self.tick(10)
        ob.step()
        self.assertEqual(self.outcome(a)[0], "launcher_stuck")
        # A late appearance refines the conclusion.
        self.probe.procs = [self.game()]
        self.tick(30)
        ob.step()
        self.assertEqual(self.outcome(a)[0], "running")

    def test_already_running_client_is_not_this_attempt(self):
        old = self.game(pid=111, start=1.0)
        self.probe.procs = [old]
        a = self.attempt()
        d.begin_baseline(a, self.probe)
        self.assertTrue(a.has("already_running"))
        ob = self.observer(a)
        self.tick(1)
        ob.step()
        self.assertFalse(a.has("game_observed"))
        self.assertEqual(self.outcome(a)[0], "in_progress")

    def test_reused_pid_is_a_new_process(self):
        self.probe.procs = [self.game(pid=111, start=1.0)]
        a = self.attempt()
        d.begin_baseline(a, self.probe)
        ob = self.observer(a)
        self.probe.procs = [self.game(pid=111, start=2.0)]
        self.tick(1)
        ob.step()
        self.assertTrue(a.has("game_observed"))

    def test_another_installation_does_not_count(self):
        other = os.path.join(self.tmp, "OtherWoW")
        a = self.attempt()
        d.begin_baseline(a, self.probe)
        ob = self.observer(a)
        self.probe.procs = [self.game(client=other)]
        self.tick(1)
        ob.step()
        self.assertFalse(a.has("game_observed"))

    def test_a_game_that_cannot_be_placed_is_a_limit_not_a_success(self):
        a = self.attempt()
        ob = self.observer(a)
        self.probe.procs = [Proc(900, "WoW.exe", 5.0, "", [], None)]
        self.tick(1)
        ob.step()
        self.assertFalse(a.has("game_observed"))
        self.assertTrue(any("cannot be placed" in t for t in a.data["limits"]))

    def test_steam_is_not_verified(self):
        a = self.attempt()
        a.data["route"] = {"runner": "custom", "target": "WoW.exe",
                           "handoff": d.handoff_of(["steam", "-applaunch", "1"])}
        ob = d.Observer(a, self.probe, FakeProc(code=0), "launcher", "steam")
        a.data["route"]["handoff"] = "Steam"
        self.tick(0.5)
        ob.step()
        outcome, text = self.outcome(a)
        self.assertEqual(outcome, "handed_off")
        self.assertIn("could not verify", text)
        self.assertIn("Proton log", text)

    def test_handoff_detection(self):
        self.assertEqual(d.handoff_of(["steam", "steam://rungameid/1"]), "Steam")
        self.assertEqual(d.handoff_of(["/usr/bin/steam"]), "Steam")
        self.assertIsNone(d.handoff_of(["/usr/bin/wine", "steampunk.exe"]))


class TestEnding(Base):
    def started(self):
        a = self.attempt()
        ob = self.observer(a, evidence_fn=lambda *x, **k: None)
        self.tick(1)
        self.probe.procs = [self.game()]
        ob.step()
        return a, ob

    def test_clean_exit(self):
        a, ob = self.started()
        self.tick(100)
        ob.step()
        self.probe.codes[self.game().key] = 0
        self.probe.procs = []
        self.tick(1)
        ob.step()
        self.assertEqual(self.outcome(a)[0], "exit_normal")
        self.assertTrue(ob.done)

    def test_early_exit_says_it_may_be_intentional(self):
        a, ob = self.started()
        self.probe.procs = []
        self.probe.codes[self.game().key] = 0
        self.tick(3)
        ob.step()
        outcome, text = self.outcome(a)
        self.assertEqual(outcome, "early_exit")
        self.assertIn("intentional", text)

    def test_error_code_is_shown_in_hex(self):
        a, ob = self.started()
        self.probe.procs = []
        self.probe.codes[self.game().key] = 0xC0000005
        self.tick(30)
        ob.step()
        outcome, text = self.outcome(a)
        self.assertEqual(outcome, "exit_error")
        self.assertIn("0xC0000005", text)

    def test_unknown_exit_is_not_called_clean_or_a_crash(self):
        a, ob = self.started()
        self.tick(40)
        ob.step()
        self.probe.procs = []
        self.tick(1)
        ob.step()
        self.assertEqual(self.outcome(a)[0], "exit_unknown")

    def test_direct_launch_uses_the_process_handle(self):
        a = self.attempt()
        proc = FakeProc()
        a.process("game", proc.pid, "WoW.exe")
        ob = self.observer(a, proc, "game", "WoW.exe", evidence_fn=lambda *x, **k: None)
        self.tick(2)
        proc.code = 7
        ob.step()
        self.assertEqual(self.outcome(a)[0], "exit_error")
        self.assertEqual(a.data["processes"][0]["exit_code"], 7)


REPORT = """==============================================================================
World of WarCraft (build 5875)

Exe:      {exe}
Time:     Nov  6, 2026  4:36:05.058 PM
User:     Oliver Example
Computer: DESKTOP-SECRET
------------------------------------------------------------------------------

ERROR #132 (0x85100084) Fatal Exception
Program:	{exe}
Exception:	0xC0000005 (ACCESS_VIOLATION) at 0023:76490147

WoWBuild: 5875
----------------------------------------
    Stack Trace (Manual)
----------------------------------------

Address  Frame    Logical addr  Module

76490147 0019F9E8 0001:0005F147 C:\\WINDOWS\\System32\\ucrtbase.dll

----------------------------------------
    Stack Trace (Using DBGHELP.DLL)
----------------------------------------

76490147 ucrtbase.dll memcpy+1287 (0x0DE02C00)
"""


class TestCrash(Base):
    def report(self, when, exe=None, name=None):
        name = name or time.strftime("%Y-%m-%d %H.%M.%S Crash.txt", time.localtime(when))
        path = self.write(f"Errors/{name}", REPORT.format(
            exe=exe or os.path.join(self.client, "WoW.exe")).encode())
        os.utime(path, (when, when))
        return name

    def test_parses_what_matters(self):
        now = time.time()
        name = self.report(now)
        r = d.parse_crash_report(os.path.join(self.client, "Errors", name))
        self.assertEqual((r["exception_code"], r["exception"], r["module"], r["build"]),
                         ("0xC0000005", "ACCESS_VIOLATION", "ucrtbase.dll", "5875"))
        self.assertEqual(d.crash_signature(r), "5875|0xC0000005|ucrtbase.dll|0001:0005F147")

    def test_fresh_report_matches_and_old_one_does_not(self):
        now = time.time()
        fresh = self.report(now + 10)
        old = self.report(now - 86400)
        matched, other = d.find_crash_reports(self.client, now, now + 20)
        self.assertEqual([r["file"] for r in matched], [fresh])
        self.assertEqual(other[0]["file"], old)
        self.assertIn("time", other[0]["why_excluded"])

    def test_report_from_another_install_is_excluded(self):
        now = time.time()
        self.report(now + 1, exe=self.client + "2\\WoW.exe")
        matched, other = d.find_crash_reports(self.client, now, now + 20)
        self.assertEqual(matched, [])
        self.assertIn("another", other[0]["why_excluded"])

    def test_malformed_report_does_not_stop_anything(self):
        now = time.time()
        path = self.write("Errors/2026-01-01 00.00.00 Crash.txt", b"\xff\xfe garbage")
        os.utime(path, (now, now))
        self.assertEqual(d.find_crash_reports(self.client, now - 5, now + 5)[0], [])

    def test_crash_decides_the_outcome(self):
        a = self.attempt()
        ob = self.observer(a)
        self.tick(1)
        self.probe.procs = [self.game()]
        ob.step()
        self.tick(30)
        self.report(self.wall.t)
        self.probe.procs = []
        ob.step()
        outcome, text = self.outcome(a)
        self.assertEqual(outcome, "crash")
        for part in ("0xC0000005", "ucrtbase.dll", "crash report"):
            self.assertIn(part, text)

    def test_old_report_never_explains_a_new_attempt(self):
        a = self.attempt()
        self.report(self.wall.t - 3600)
        ob = self.observer(a)
        self.tick(1)
        self.probe.procs = [self.game()]
        ob.step()
        self.tick(30)
        self.probe.procs = []
        ob.step()
        self.assertEqual(self.outcome(a)[0], "exit_unknown")
        self.assertTrue(a.data["crash"]["older_reports"])

    def test_report_with_a_client_already_running_is_ambiguous(self):
        self.probe.procs = [self.game(pid=1, start=1.0)]
        a = self.attempt()
        d.begin_baseline(a, self.probe)
        self.report(self.wall.t + 1)
        d.collect_evidence(a.data, until=self.wall.t + 5, events_fn=lambda *x: [])
        self.assertEqual(a.data["crash"]["reports"], [])
        self.assertIn("already running", a.data["crash"]["older_reports"][0]["why_excluded"])


EVENT = """<Event xmlns='http://schemas.microsoft.com/win/2004/08/events/event'><System>
<Provider Name='Application Error'/><TimeCreated SystemTime='{stamp}'/></System><EventData>
<Data>{app}</Data><Data>1.12.1.5875</Data><Data>0</Data><Data>nampower.dll</Data>
<Data>4.6.3.0</Data><Data>0</Data><Data>c0000005</Data><Data>00012345</Data><Data>{pid}</Data>
<Data>0</Data><Data>{path}</Data><Data>x</Data><Data>y</Data></EventData></Event>"""


class TestWindowsEvents(Base):
    def event(self, when, app="WoW.exe", path=None, pid="0x384"):
        from datetime import datetime, timezone
        stamp = datetime.fromtimestamp(when, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.0000000Z")
        return EVENT.format(stamp=stamp, app=app, pid=pid,
                            path=path or os.path.join(self.client, "WoW.exe"))

    def ask(self, xml, pids=()):
        now = time.time()
        return d.windows_events(self.client, now - 5, now + 5, pids,
                                run=lambda q: xml)

    def test_matching_fault_fields(self):
        found = self.ask(self.event(time.time()), pids=[0x384])
        self.assertEqual(len(found), 1)
        e = found[0]
        self.assertEqual((e["module"], e["exception_code"], e["offset"], e["pid"]),
                         ("nampower.dll", "0xc0000005", "00012345", 0x384))

    def test_unrelated_events_excluded(self):
        for xml in (self.event(time.time(), app="chrome.exe"),
                    self.event(time.time(), path=r"E:\Other\WoW.exe"),
                    self.event(time.time() - 3600)):
            self.assertEqual(self.ask(xml), [])
        self.assertEqual(self.ask(self.event(time.time()), pids=[1]), [])

    def test_unavailable_log_is_an_empty_answer(self):
        def boom(_q):
            raise OSError("wevtutil not found")
        self.assertEqual(d.windows_events(self.client, 0, 1, run=boom), [])
        self.assertEqual(self.ask("<Event><broken"), [])


class TestRecovery(Base):
    def left_open(self):
        a = self.attempt()
        a.process("game", 900, "WoW.exe", 5.0)
        a.event("spawned", role="launcher", pid=500, name="VanillaFixes.exe")
        a.event("game_observed", pid=900)
        return a

    def test_crash_found_by_the_next_session(self):
        self.wall.t -= 100
        a = self.left_open()
        name = time.strftime("%Y-%m-%d %H.%M.%S Crash.txt", time.localtime(self.wall.t + 20))
        path = self.write(f"Errors/{name}", REPORT.format(
            exe=os.path.join(self.client, "WoW.exe")).encode())
        os.utime(path, (self.wall.t + 20, self.wall.t + 20))
        self.assertEqual(d.recover(self.store, FakeProbe()), [])
        data = self.store.load(a.id)
        self.assertTrue(data["finished"])
        self.assertEqual(data["outcome"], "crash")

    def test_nothing_found_says_so(self):
        a = self.left_open()
        d.recover(self.store, FakeProbe())
        data = self.store.load(a.id)
        self.assertEqual(data["outcome"], "unavailable")
        self.assertIn("stopped watching", data["explanation"])

    def test_a_game_still_running_is_watched_again(self):
        a = self.left_open()
        probe = FakeProbe()
        probe.procs = [Proc(900, "WoW.exe", 5.0, "", [], None)]
        resume = d.recover(self.store, probe)
        self.assertEqual([r["id"] for r in resume], [a.id])
        ob = d.watch_again(self.store, resume[0], probe)
        self.assertEqual(ob.game.pid, 900)
        probe.procs = []
        ob.evidence_fn = lambda *x, **k: None
        ob.step()
        self.assertTrue(ob.done)
        self.assertTrue(self.store.load(a.id)["finished"])

    def test_a_reused_pid_is_not_the_game(self):
        a = self.left_open()
        probe = FakeProbe()
        probe.procs = [Proc(900, "WoW.exe", 77.0, "", [], None)]
        self.assertEqual(d.recover(self.store, probe), [])
        self.assertTrue(self.store.load(a.id)["finished"])

    def test_a_later_attempts_crash_is_not_this_ones(self):
        a = self.left_open()
        self.wall.t += 600
        later = self.attempt()
        later.finish()
        when = self.wall.t + 5
        name = time.strftime("%Y-%m-%d %H.%M.%S Crash.txt", time.localtime(when))
        path = self.write(f"Errors/{name}", REPORT.format(
            exe=os.path.join(self.client, "WoW.exe")).encode())
        os.utime(path, (when, when))
        d.recover(self.store, FakeProbe())
        self.assertEqual(self.store.load(a.id)["outcome"], "unavailable")

    def test_finished_attempts_are_left_alone(self):
        a = self.attempt()
        a.finish()
        before = self.store.load(a.id)["last_saved"]
        d.recover(self.store, FakeProbe())
        self.assertEqual(self.store.load(a.id)["last_saved"], before)


class TestComponents(Base):
    def test_present_expected_and_listed_are_separate(self):
        self.write("WoW.exe", PE_X86)
        self.write("VanillaFixes.exe", PE_X86)
        self.write("dlls.txt", b"nampower.dll\nmods/OctoLogin.dll\nmissing.dll\n")
        self.write("nampower.dll", PE_X86)
        self.write("mods/OctoLogin.dll", PE_X86)
        rows = {c["file"]: c for c in d.launch_components(self.client, "VanillaFixes.exe")}
        self.assertTrue(rows["VanillaFixes.exe"]["expected_by_route"])
        self.assertFalse(rows["VfPatcher.dll"]["present"])
        self.assertTrue(rows["nampower.dll"]["listed_in_dlls_txt"])
        self.assertEqual(rows["WoW.exe"]["machine"], "x86")
        self.assertEqual(len(rows["WoW.exe"]["sha256"]), 64)
        found = d.component_findings(list(rows.values()), "VanillaFixes.exe")
        self.assertTrue(any("VfPatcher.dll is missing" in f for f in found))
        self.assertTrue(any("lists missing.dll" in f for f in found))

    def test_direct_launch_does_not_need_the_loader(self):
        self.write("WoW.exe", PE_X86)
        rows = d.launch_components(self.client, "WoW.exe")
        self.assertEqual(d.component_findings(rows, "WoW.exe"), [])

    def test_wrong_architecture_and_a_bad_binary(self):
        self.write("WoW.exe", b"garbage")
        self.write("VanillaFixes.exe", PE_X64)
        rows = d.launch_components(self.client, "VanillaFixes.exe")
        found = " ".join(d.component_findings(rows, "VanillaFixes.exe"))
        self.assertIn("error 193", found)
        self.assertIn("x64", found)


class TestRedaction(Base):
    def test_paths_names_and_secrets(self):
        red = d.Redactor(r"D:\Games\OctoWoW", home=r"C:\Users\Alice Smith")
        cases = [
            (r"D:\Games\OctoWoW\WoW.exe", "Alice", r"<client>"),
            (r"d:/games/octowow/Data/a.mpq", "octowow", "<client>"),
            (r"C:\Users\Alice Smith\AppData\x.log", "Alice", "<home>"),
            (r"C:\Users\Bob\Desktop\run.bat", "Bob", "<user>"),
            ("/home/carol/.wine/drive_c/users/carol/x", "carol", "<user>"),
            (r"Z:\home\dave\Games\wow", "dave", "<user>"),
            ("https://bob:hunter2@example.com/path?token=abc", "hunter2", "example.com"),
            ("https://example.com/a?auth=zzz&x=1", "zzz", "example.com"),
            ("password=hunter2 next", "hunter2", "next"),
            ("Authorization: Bearer abc.def", "abc.def", "Authorization"),
            ("--token abc123", "abc123", "--token"),
            ("User:     Alice\nComputer: BOX", "BOX", "<removed>"),
            (r"WTF\Account\MYACCOUNT\Realm\Character\x", "MYACCOUNT", "<account>"),
            ("mail me@example.org now", "me@example.org", "<email>"),
        ]
        for text, gone, kept in cases:
            out = red.text(text)
            self.assertNotIn(gone, out, text)
            self.assertIn(kept, out, text)

    def test_the_user_name_wherever_it_turns_up(self):
        red = d.Redactor("", home=r"C:\Users\Alice Smith")
        self.assertEqual(red.text(r"E:\temp\pytest-of-Alice Smith\x"),
                         r"E:\temp\pytest-of-<user>\x")

    def test_wine_spelling_of_the_client(self):
        red = d.Redactor("/home/eve/Games/wow", home="/home/eve")
        self.assertEqual(red.text(r"Z:\home\eve\Games\wow\WoW.exe"), r"<client>\WoW.exe")

    def test_argv(self):
        red = d.Redactor(r"D:\Games\OctoWoW", home=r"C:\Users\Alice")
        out = d.sanitise_argv([r"D:\Games\OctoWoW\WoW.exe", "--password", "hunter2",
                               "-token=abc", "https://x.io/a?key=1"], red)
        joined = " ".join(out)
        for gone in ("hunter2", "abc", "key=1", "Games"):
            self.assertNotIn(gone, joined)

    def test_only_allowlisted_environment(self):
        red = d.Redactor("", home="/home/eve")
        env = {"WINEPREFIX": "/home/eve/pfx", "AWS_SECRET": "x", "PATH": "/bin",
               "DISCORD_TOKEN": "y", "GAMEID": "umu-default"}
        self.assertEqual(d.recorded_env(env, red),
                         {"WINEPREFIX": "<home>/pfx", "GAMEID": "umu-default"})


class TestBundle(Base):
    def setUp(self):
        super().setUp()
        self.dest = os.path.join(self.tmp, "report.zip")

    def read(self):
        with zipfile.ZipFile(self.dest) as z:
            self.assertIsNone(z.testzip())
            return {n: z.read(n).decode("utf-8", "replace") for n in z.namelist()}

    def export(self, data, scan=None, lines=None, home=None):
        return d.export_bundle(self.dest, data, scan, self.store, lines, home=home)

    def test_valid_when_wow_never_spawned_and_no_scan(self):
        a = self.attempt()
        a.block("executable", "not found")
        self.export(self.store.load(a.id))
        members = self.read()
        self.assertIn("summary.txt", members)
        self.assertIn("timeline.json", members)
        self.assertNotIn("client-scan.json", members)
        self.assertIn("client-scan.json: no scan", members["omitted.txt"])
        self.assertEqual(json.loads(members["timeline.json"])["schema"], d.SCHEMA)

    def test_realistic_private_data_does_not_leave(self):
        home = os.path.join(self.tmp, "Users", "AliceSmith")
        a = self.attempt()
        a.redactor = d.Redactor(self.client, home)
        output = a.output_path()
        with open(output, "w", encoding="utf-8") as f:
            f.write(f"loading {self.client}\\WoW.exe\n"
                    f"C:\\Users\\AliceSmith\\pfx\n"
                    f"token=SECRETTOKEN https://u:SECRETPW@x.io/y?session=SECRETQ\n"
                    f"WTF\\Account\\MYACCOUNT\\Realm\\Char\\SavedVariables\\x.lua\n")
        when = self.wall.t + 1
        name = time.strftime("%Y-%m-%d %H.%M.%S Crash.txt", time.localtime(when))
        path = self.write(f"Errors/{name}", REPORT.format(
            exe=os.path.join(self.client, "WoW.exe")).encode())
        os.utime(path, (when, when))
        self.write(f"Errors/{name[:-4]}.dmp", b"DUMPDATA")
        self.write("WoW_d3d9.log", f"info: {home}\\x password=SECRETPW\n".encode())
        a.route(type("Plan", (), {
            "runner": "windows", "cwd": self.client,
            "argv": [os.path.join(self.client, "WoW.exe"), "--password", "SECRETARG"],
            "env": {"WINEPREFIX": home + "/pfx", "API_KEY": "SECRETENV"}}),
            os.path.join(self.client, "WoW.exe"), "WoW.exe")
        d.collect_evidence(a.data, until=when + 5, events_fn=lambda *x: [])
        a.save()
        self.export(self.store.load(a.id), lines=[f"Launched from {self.client}\n"], home=home)
        members = self.read()
        self.assertTrue(any(n.startswith("crash-reports/") for n in members))
        self.assertIn("logs/WoW_d3d9.log", members)
        joined = "\n".join(members.values())
        base = os.path.basename(self.tmp)
        for gone in ("SECRETTOKEN", "SECRETPW", "SECRETQ", "SECRETARG", "SECRETENV",
                     "AliceSmith", "MYACCOUNT", "DESKTOP-SECRET", "Oliver Example",
                     "DUMPDATA", base):
            self.assertNotIn(gone, joined, gone)
        self.assertFalse(any(n.endswith(".dmp") for n in members))

    def test_a_diagnostic_launchs_own_logs_are_included_and_filtered(self):
        a = self.attempt(diagnostic=True)
        folder = os.path.dirname(a.output_path())
        with open(os.path.join(folder, "steam-1.log"), "w", encoding="utf-8") as f:
            f.write("loaddll: C:\\Users\\AliceSmith\\x.dll token=SECRET\n")
        with open(os.path.join(folder, "notes.txt"), "w", encoding="utf-8") as f:
            f.write("NOTALOG")
        self.export(self.store.load(a.id))
        members = self.read()
        self.assertIn("loaddll", members["runner-logs/steam-1.log"])
        joined = "\n".join(members.values())
        for gone in ("AliceSmith", "SECRET", "NOTALOG"):
            self.assertNotIn(gone, joined)

    def test_unknown_logs_and_forbidden_places_are_not_read(self):
        a = self.attempt()
        self.write("WTF/secret.log", b"PRIVATE")
        a.data["logs"] = [{"file": "WTF/secret.log"}, {"file": "other.log"}]
        self.export(a.data)
        members = self.read()
        self.assertNotIn("PRIVATE", "\n".join(members.values()))
        self.assertIn("not a known diagnostic source", members["omitted.txt"])

    def test_size_limits(self):
        a = self.attempt()
        with open(a.output_path(), "w") as f:
            f.write("x" * (3 * 1024 * 1024))
        self.export(self.store.load(a.id))
        with zipfile.ZipFile(self.dest) as z:
            size = z.getinfo("runner-output.txt").file_size
        self.assertLessEqual(size, d.MAX_MEMBER_BYTES)
        with mock.patch.object(d, "MAX_BUNDLE_BYTES", 2000):
            self.export(self.store.load(a.id))
        self.assertIn("size limit", self.read()["omitted.txt"])

    def test_failure_leaves_no_half_a_zip(self):
        a = self.attempt()
        with mock.patch.object(d, "support_summary", side_effect=RuntimeError("no")):
            with self.assertRaises(RuntimeError):
                self.export(a.data)
        self.assertFalse(os.path.exists(self.dest))
        self.assertEqual([n for n in os.listdir(self.tmp) if n.startswith(".eqdiag")], [])

    def test_member_names_are_safe(self):
        self.assertEqual(d._safe_member("../../evil\\..\\x.txt"), "x.txt")
        self.assertEqual(d._safe_member("a:b*c.log"), "a_b_c.log")

    def test_the_example_a_player_would_send(self):
        a = self.attempt()
        a.route(type("Plan", (), {"runner": "windows", "cwd": self.client,
                                  "argv": [os.path.join(self.client, "VanillaFixes.exe")],
                                  "env": None}),
                os.path.join(self.client, "VanillaFixes.exe"), "VanillaFixes.exe")
        proc = FakeProc()
        ob = d.Observer(a, self.probe, proc, "launcher", "VanillaFixes.exe",
                        evidence_fn=lambda *x, **k: None)
        a.process("launcher", proc.pid, "VanillaFixes.exe")
        self.tick(0.8)
        proc.code = 0
        ob.step()
        self.tick(d.OBSERVE_WINDOW_S)
        ob.step()
        text = d.Redactor(self.client, "").text(d.support_summary(a.data))
        for part in ("never observed", "VanillaFixes.exe", "launcher_exited",
                     "not_observed_in_window", "<client>", "Not run"):
            self.assertIn(part, text)


class TestProbes(unittest.TestCase):
    """The real thing, on whatever platform runs the tests."""

    def child(self, *code):
        p = subprocess.Popen([sys.executable, "-c", *code])
        self.addCleanup(p.wait)
        self.addCleanup(p.kill)
        return p

    @unittest.skipUnless(platforms.WINDOWS and not os.environ.get("CI"),
                         "Windows, with a desktop to show a message box on")
    def test_windows_snapshot_exit_code_and_message_box(self):
        probe = d.WindowsProbe()
        p = self.child("import ctypes;ctypes.windll.user32.MessageBoxW"
                       "(0,'VfPatcher.dll not found.','VanillaFixes',0)")
        exe = os.path.basename(sys.executable)
        for _ in range(100):
            found = [x for x in probe.snapshot([exe]) if x.pid == p.pid]
            texts = probe.window_texts(p.pid)
            if found and len(texts) >= 2:
                break
            time.sleep(0.1)
        self.assertEqual(os.path.normcase(found[0].exe),
                         os.path.normcase(os.path.realpath(sys.executable)))
        self.assertIsNotNone(found[0].start)
        self.assertEqual(texts[0], "VanillaFixes")
        self.assertIn("VfPatcher.dll not found.", texts)
        probe.hold(found[0])
        self.assertIsNone(probe.exit_code(found[0]))
        p.kill()
        p.wait()
        self.assertEqual(probe.exit_code(found[0]), 1)

    @unittest.skipUnless(platforms.WINDOWS, "Windows")
    def test_windows_modules_of_a_real_process(self):
        probe = d.WindowsProbe()
        p = self.child("import time;time.sleep(30)")
        for _ in range(50):
            mods = probe.modules(p.pid)
            if mods:
                break
            time.sleep(0.1)
        self.assertTrue(any(m.lower().endswith("kernel32.dll") for m in mods))

    @unittest.skipIf(platforms.WINDOWS, "needs Unix paths")
    def test_wine_paths(self):
        here = Proc(1, "wow.exe", 1.0, r"Z:\home\me\wow\WoW.exe",
                    [r"Z:\home\me\wow\WoW.exe"], None)
        self.assertIs(d.belongs_to_client(here, "/home/me/wow"), True)
        self.assertIs(d.belongs_to_client(here, "/home/me/other"), False)
        inside = Proc(2, "wow.exe", 1.0, r"C:\Game\WoW.exe", [r"C:\Game\WoW.exe"], "/home/me/wow")
        self.assertIs(d.belongs_to_client(inside, "/home/me/wow"), True)
        unknown = Proc(3, "wow.exe", 1.0, r"C:\Game\WoW.exe", [r"C:\Game\WoW.exe"], None)
        self.assertIsNone(d.belongs_to_client(unknown, "/home/me/wow"))


def manifest_for(root, files, piece_length=16):
    import hashlib
    blob = b"".join(data for _parts, data in files)
    pieces = b"".join(hashlib.sha1(blob[i:i + piece_length]).digest()
                      for i in range(0, len(blob), piece_length))
    return {"name": "test manifest", "piece_length": piece_length, "pieces": pieces,
            "files": [(parts, len(data)) for parts, data in files]}


class TestScan(Base):
    def run_scan(self, **kw):
        return clientscan.scan(self.client, **kw)

    def codes(self, result, level=None):
        return {f["code"] for f in result["findings"]
                if level is None or f["level"] == level}

    def test_finds_nested_files(self):
        self.write("WoW.exe", PE_X86)
        self.write("Interface/AddOns/Deep/Deeper/Deepest/a.lua")
        result = self.run_scan()
        self.assertEqual(result["coverage"]["files"], 2)

    def test_zero_byte_and_bad_files_are_confirmed(self):
        self.write("WoW.exe", b"garbage")
        self.write("empty.dll", b"")
        self.write("Data/cut.mpq", MPQ_OK[:8] + (9999).to_bytes(4, "little") + b"\0" * 8)
        self.write("Data/notmpq.mpq", b"hello there")
        result = self.run_scan()
        self.assertTrue({"bad_binary", "empty_file", "bad_archive"} <=
                        self.codes(result, "confirmed"))
        why = " ".join(f["why"] for f in result["findings"])
        self.assertIn("cut short", why)
        self.assertIn("does not start like an MPQ", why)

    def test_one_unreadable_file_does_not_end_the_scan(self):
        self.write("WoW.exe", PE_X86)
        self.write("a.dll", PE_X86)
        self.write("z.dll", b"garbage")
        real = clientscan.pe_machine

        def picky(path):
            if path.endswith("a.dll"):
                raise PermissionError(13, "denied")
            return real(path)
        with mock.patch.object(clientscan, "pe_machine", picky):
            result = self.run_scan()
        self.assertIn("unreadable", self.codes(result))
        self.assertIn("bad_binary", self.codes(result))
        self.assertEqual(result["coverage"]["unreadable"], 1)

    def test_custom_content_is_not_called_a_problem(self):
        self.write("WoW.exe", PE_X86)
        self.write("Data/patch-Z.mpq", MPQ_OK + b"\0" * 8)
        self.write("Interface/AddOns/Mine/Mine.toc", b"## Title: mine")
        self.write("unfamiliar.dll", PE_X86)
        result = self.run_scan()
        self.assertEqual(self.codes(result, "confirmed"), set())
        self.assertEqual(self.codes(result, "possible"), set())

    def test_links_are_not_followed_and_say_so(self):
        outside = os.path.join(self.tmp, "outside")
        os.makedirs(outside)
        with open(os.path.join(outside, "secret.dll"), "wb") as f:
            f.write(b"garbage")
        self.write("WoW.exe", PE_X86)
        link = os.path.join(self.client, "linked")
        try:
            platforms.link_dir(link, outside)
        except (OSError, RuntimeError) as e:
            self.skipTest(f"cannot make a link here: {e}")
        loop = os.path.join(self.client, "loop")
        platforms.link_dir(loop, self.client)
        result = self.run_scan()
        self.assertIn("link_skipped", self.codes(result))
        self.assertEqual(result["coverage"]["skipped"], 2)
        self.assertEqual(result["coverage"]["files"], 1)
        self.assertNotIn("bad_binary", self.codes(result))

    def test_case_collision(self):
        collide = [("Data/a.mpq", "a.mpq"), ("Data/A.MPQ", "A.MPQ")]

        class Entry:
            def __init__(self, name):
                self.name = name

            def stat(self, follow_symlinks=False):
                return os.stat_result((0, 0, 0, 0, 0, 0, 100, 0, 0, 0))

        def fake_walk(root, cancel=None, on_dir=None):
            return iter([(rel, Entry(n)) for rel, n in collide]), [], []
        self.write("Data/a.mpq", MPQ_OK)
        with mock.patch.object(clientscan, "walk", fake_walk), \
                mock.patch.object(clientscan, "check_mpq", return_value=None):
            result = self.run_scan()
        self.assertIn("case_collision", self.codes(result, "confirmed"))

    def test_duplicate_and_misplaced_files(self):
        self.write("WoW.exe", PE_X86)
        self.write("Data/WoW.exe", PE_X86)
        self.write("stray.mpq", MPQ_OK)
        self.write("Interface/AddOns/Foo-main/Foo/Foo.toc", b"x")
        result = self.run_scan()
        self.assertTrue({"duplicate_critical", "misplaced_mpq", "nested_addon"} <=
                        self.codes(result, "possible"))

    def test_folders_the_game_does_not_use_are_listed_not_judged(self):
        # A real client held backups and a tool's work files: a damaged file
        # the game never opens is not a problem with the game.
        self.write("WoW.exe", PE_X86)
        self.write("_backup_2026/old.dll", b"garbage")
        self.write("_backup_2026/empty.exe", b"")
        self.write("_backup_2026/WoW.exe", PE_X86)
        self.write("_backup_2026/stray.mpq", b"nope")
        self.write(".tool/work/fixture.exe", b"garbage")
        result = self.run_scan()
        self.assertEqual(self.codes(result, "confirmed"), set())
        self.assertEqual(self.codes(result, "possible"), set())
        note = next(f for f in result["findings"] if f["code"] == "other_folders")
        self.assertIn("5 files", note["why"])
        self.assertIn("_backup_2026", note["evidence"])
        self.assertEqual(result["coverage"]["files"], 6)

    def test_a_private_folder_in_a_backup_stays_private(self):
        self.write("WoW.exe", PE_X86)
        self.write("Data/WTF/Account/MYACCOUNT/Realm/Char/x.toc", b"")
        result = self.run_scan()
        self.assertNotIn("MYACCOUNT", json.dumps(d.exportable_scan(result)))
        self.assertEqual(result["coverage"]["private_files"], 1)

    def test_many_links_are_one_line(self):
        self.write("WoW.exe", PE_X86)
        links = [f"l{i}" for i in range(30)]

        class Entry:
            name = "WoW.exe"

            def stat(self, follow_symlinks=False):
                return os.stat_result((0, 0, 0, 0, 0, 0, 100, 0, 0, 0))

        def fake_walk(root, cancel=None, on_dir=None):
            return iter([("WoW.exe", Entry())]), links, []
        with mock.patch.object(clientscan, "walk", fake_walk):
            result = self.run_scan()
        notes = [f for f in result["findings"] if f["code"] == "link_skipped"]
        self.assertEqual(len(notes), 1)
        self.assertIn("30", notes[0]["why"])
        self.assertEqual(result["coverage"]["skipped"], 30)

    def test_client_one_folder_down_is_named(self):
        self.write("OctoWoW/WoW.exe", PE_X86)
        result = self.run_scan()
        finding = next(f for f in result["findings"] if f["code"] == "no_game_exe")
        self.assertIn("one level too high", finding["why"])

    def test_manifest_missing_and_wrong_size(self):
        self.write("WoW.exe", PE_X86)
        self.write("Data/base.mpq", MPQ_OK)
        manifest = {"name": "m", "files": [
            (["WoW.exe"], len(PE_X86) + 5), (["Data", "base.mpq"], len(MPQ_OK)),
            (["Data", "gone.mpq"], 10), (["nampower.dll"], 3)]}
        result = self.run_scan(manifest=manifest, protected={"wow.exe", "nampower.dll"})
        paths = {f["path"]: f["code"] for f in result["findings"]
                 if f["code"].startswith("manifest")}
        # WoW.exe is patched, nampower.dll belongs to the mod system.
        self.assertEqual(paths, {"Data/gone.mpq": "manifest_missing"})
        self.assertEqual(result["coverage"]["manifest"], "m")

    def test_no_manifest_says_what_was_not_checked(self):
        self.write("WoW.exe", PE_X86)
        result = self.run_scan()
        self.assertIn("no_manifest", self.codes(result, "info"))
        self.assertIsNone(result["coverage"]["manifest"])

    def test_deep_check_catches_same_size_damage(self):
        data = [(["a.dat"], b"A" * 40), (["b.dat"], b"B" * 40), (["c.mpq"], b"C" * 40)]
        manifest = manifest_for(self.client, data)
        self.write("WoW.exe", PE_X86)
        self.write("a.dat", b"A" * 40)
        self.write("b.dat", b"B" * 20 + b"X" + b"B" * 19)
        self.write("c.mpq", MPQ_OK[:4] + b"C" * 36)
        quick = self.run_scan(mode=clientscan.QUICK, manifest=manifest)
        self.assertNotIn("manifest_hash", self.codes(quick))
        deep = self.run_scan(mode=clientscan.DEEP, manifest=manifest)
        bad = [f["path"] for f in deep["findings"] if f["code"] == "manifest_hash"]
        self.assertEqual(bad, ["b.dat"])
        self.assertFalse(deep["coverage"]["mpq_verified"])
        self.assertEqual(deep["coverage"]["hash_skipped"], 1)
        every = self.run_scan(mode=clientscan.DEEP, manifest=manifest, include_mpq=True)
        self.assertTrue(every["coverage"]["mpq_verified"])
        self.assertIn("c.mpq", [f["path"] for f in every["findings"]
                                if f["code"] == "manifest_hash"])

    def test_cancelling_keeps_what_was_found(self):
        for i in range(20):
            self.write(f"f{i:02}.dll", b"")
        cancel = threading.Event()
        seen = []

        def progress(text, fraction=None):
            seen.append(text)
        real = clientscan.walk

        def walking(root, c=None, on_dir=None):
            gen, links, errors = real(root, c, on_dir)

            def stop_after_first():
                for n, item in enumerate(gen):
                    if n == 3:
                        cancel.set()
                    if cancel.is_set():
                        raise clientscan.Cancelled()
                    yield item
            return stop_after_first(), links, errors
        with mock.patch.object(clientscan, "walk", walking):
            result = clientscan.scan(self.client, cancel=cancel, progress=progress)
        self.assertTrue(result["coverage"]["cancelled"])
        self.assertEqual(result["coverage"]["files"], 3)

    def test_mod_receipts(self):
        self.write("WoW.exe", PE_X86)
        self.write("kept.dll", PE_X86)
        hash_of = lambda client, files: "now"
        mods = {"gone": {"installed_files": ["gone.dll"], "enabled": True},
                "edited": {"installed_files": ["kept.dll"], "enabled": True,
                           "content_hash": "then", "hash_baseline": "adoption"},
                "same": {"installed_files": ["kept.dll"], "enabled": True,
                         "content_hash": "now"},
                "off": {"installed_files": ["gone.dll"], "enabled": False}}
        result = self.run_scan(mods=mods, registry_names={"gone": "Gone Mod"},
                               files_hash=hash_of)
        self.assertEqual(len([f for f in result["findings"]
                              if f["code"] == "mod_files_missing"]), 1)
        self.assertEqual(len([f for f in result["findings"]
                              if f["code"] == "mod_files_changed"]), 1)

    def test_private_names_do_not_reach_the_export_or_inventory(self):
        self.write("WoW.exe", PE_X86)
        self.write("WTF/Account/MYACCOUNT/SavedVariables/x.lua", b"secret")
        self.write("WTF/Account/MYACCOUNT/Realm/Char/empty.toc", b"")
        inv = os.path.join(self.tmp, "inv.txt")
        result = self.run_scan(inventory_path=inv)
        self.assertEqual(result["coverage"]["private_files"], 2)
        with open(inv, encoding="utf-8") as f:
            self.assertNotIn("MYACCOUNT", f.read())
        self.assertNotIn("MYACCOUNT", json.dumps(d.exportable_scan(result)))

    def test_login_doctor_results_are_derived_findings(self):
        self.write("WoW.exe", PE_X86)
        result = self.run_scan(doctor_checks=[("confirmed", "realmlist uses the old address", "client")])
        f = next(x for x in result["findings"] if x["code"] == "login_client")
        self.assertEqual(f["evidence"], "Login Doctor")

    def test_a_large_file_is_listed_without_being_read(self):
        path = self.write("Data/huge.bin", b"")
        with open(path, "wb") as f:
            f.truncate(256 * 1024 ** 2)
        self.write("WoW.exe", PE_X86)
        started = time.monotonic()
        result = self.run_scan()
        self.assertLess(time.monotonic() - started, 5)
        self.assertGreaterEqual(result["coverage"]["bytes"], 256 * 1024 ** 2)

    def test_a_long_log_line_is_not_slow_to_filter(self):
        started = time.monotonic()
        out = d.Redactor("", home="").text("x" * (2 * 1024 * 1024) + "\nuser: me")
        self.assertLess(time.monotonic() - started, 3)
        self.assertLess(len(out), 10000)
        self.assertNotIn(" me", out)


if __name__ == "__main__":
    unittest.main()
