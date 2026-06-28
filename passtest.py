#!/usr/bin/env python3
"""passtest.py — watch a directory for passgen wordlist chunks and run hashcat
against a TrueCrypt/VeraCrypt volume header until a password is found.

Workflow:
  1. passgen.py -o wordlist.txt -s ...   # streams wordlist_001.txt, wordlist_002.txt, ...
  2. passtest.py                          # watches dict_dir, feeds each chunk to hashcat

It reads passtest.json (next to this script by default), monitors `dict_dir`
for files named "<wordlist>_###.txt", and runs one hashcat "job" per configured
mode/argument-set against `header_bin`. Jobs on distinct GPUs run in parallel
(one hashcat instance per device); jobs that share a device run sequentially.

If any job recovers a password, it is printed to stdout, appended to the found
file, and the program exits. Otherwise the wordlist is renamed
"DONE_<wordlist>_###.txt" and the next chunk is processed. When no un-processed
chunk remains the program keeps polling (passgen may still be generating).

Cross-platform: Windows and Linux (pure stdlib, no shell invocation).
"""
import os
import re
import sys
import json
import time
import logging
import argparse
import threading
import subprocess
from pathlib import Path
from datetime import datetime

LOG = logging.getLogger("passtest")

DEFAULT_CONFIG_NAME = "passtest.json"
REQUIRED_KEYS = ("header_bin", "hcat_path")  # dict_dir/wordlist only required when dictionary jobs are present


def setup_logging(verbose=False):
    for stream in (sys.stdout, sys.stderr):  # robust to non-ASCII passwords/paths on Windows consoles
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except (AttributeError, ValueError):
            pass
    handler = logging.StreamHandler(sys.stderr)  # stderr keeps stdout clean for the password
    handler.setFormatter(logging.Formatter("[%(asctime)s] (%(levelname)s) %(message)s",
                                            "%Y/%m/%d %H:%M:%S"))
    LOG.addHandler(handler)
    LOG.setLevel(logging.DEBUG if verbose else logging.INFO)


class Passtest:

    def __init__(self, config, config_dir):
        self.config_dir = Path(config_dir)
        self.dict_dir = self._resolve(config["dict_dir"]) if "dict_dir" in config else None
        self.stem = str(config.get("wordlist", ""))
        self.header_bin = self._resolve(config["header_bin"])
        self.hcat_path: str = str(config["hcat_path"])
        self.poll_interval = float(config.get("poll_interval", 5))
        self.found_file = self._resolve(config.get("found_file", "found.txt"))
        self.out_dir = self._resolve(config.get("out_dir", ".passtest_out"))
        self.extra_global = [str(a) for a in config.get("hcat_args", [])]  # appended to every job
        self.jobs = self._build_jobs(config)
        self.mask_jobs = [j for j in self.jobs if j["attack_mode"] == 3]
        self.dict_jobs  = [j for j in self.jobs if j["attack_mode"] == 0]
        self.mask_job_groups = self._group_by_device(self.mask_jobs)
        self.dict_job_groups = self._group_by_device(self.dict_jobs)
        # matches "<stem>_<digits>.txt" but NOT "DONE_<stem>_<digits>.txt"
        self._wl_re = re.compile(r"^" + re.escape(self.stem) + r"_(\d+)\.txt$", re.IGNORECASE) if self.stem else None
        self._procs_lock = threading.Lock()
        self._active_procs = set()
        self._shutdown = False

    # ------------------------------------------------------------------ config

    def _resolve(self, p):  # resolve paths relative to the config file's directory
        path = Path(p)
        return path if path.is_absolute() else (self.config_dir / path)

    def _build_jobs(self, config):
        jobs = []
        raw = config.get("jobs")
        if raw:  # preferred form: a list of {mode, attack_mode?, mask?, device?, args?, rules?}
            for j in raw:
                mode = j.get("mode")
                if mode is None:
                    LOG.warning("Skipping job without a 'mode': %r", j)
                    continue
                rules_raw = j.get("rules", [])
                if isinstance(rules_raw, str):
                    rules_raw = [rules_raw]
                attack_mode = int(j.get("attack_mode", 0))
                mask = str(j["mask"]) if "mask" in j else None
                if attack_mode == 3 and not mask:
                    LOG.error("Job with attack_mode 3 requires a 'mask' field: %r", j)
                    sys.exit(1)
                jobs.append({"mode": int(mode),
                             "attack_mode": attack_mode,
                             "mask": mask,
                             "device": j.get("device"),
                             "args": [str(a) for a in j.get("args", [])],
                             "rules": [str(self._resolve(r)) for r in rules_raw]})
        else:  # fallback: a flat 'modes' list, optionally spread over 'devices'
            modes = config.get("modes", [])
            devices = config.get("devices") or [None]
            for i, mode in enumerate(modes):
                jobs.append({"mode": int(mode),
                             "attack_mode": 0,
                             "mask": None,
                             "device": devices[i % len(devices)],
                             "args": [],
                             "rules": []})
        if not jobs:
            LOG.error("No 'jobs' or 'modes' defined in configuration.")
            sys.exit(1)
        return jobs

    def _group_by_device(self, jobs):  # one worker per device → parallel across GPUs, serial within a GPU
        groups = {}
        for job in jobs:
            groups.setdefault(job["device"], []).append(job)
        return groups

    def _validate_environment(self):
        if not self.header_bin.is_file():
            LOG.error("Header file not found: %s", self.header_bin)
            sys.exit(1)
        exe = self.hcat_path
        has_sep = (os.sep in exe) or bool(os.altsep and os.altsep in exe)
        if has_sep:
            if not Path(exe).is_file():
                LOG.error("hashcat executable not found: %s", exe)
                sys.exit(1)
        else:
            path_dirs = (os.environ.get("PATH") or "").split(os.pathsep)
            if not any(os.access(os.path.join(d, exe), os.X_OK) for d in path_dirs):
                LOG.error("hashcat not found on PATH: %s", exe)
                sys.exit(1)
        if self.dict_jobs:
            if not self.dict_dir:
                LOG.error("Dictionary jobs require 'dict_dir' in the configuration.")
                sys.exit(1)
            if not self.stem:
                LOG.error("Dictionary jobs require 'wordlist' in the configuration.")
                sys.exit(1)
            if not self.dict_dir.exists():
                LOG.warning("dict_dir does not exist yet: %s (will keep polling)", self.dict_dir)

    # ------------------------------------------------------------------ scanning

    def _pending_wordlists(self):  # undone chunks, sorted by ### ascending
        try:
            entries = os.listdir(self.dict_dir)
        except (FileNotFoundError, NotADirectoryError):
            return []
        matched = []
        for name in entries:
            m = self._wl_re.match(name)
            if m:
                matched.append((int(m.group(1)), name))
        matched.sort()
        return [self.dict_dir / name for _, name in matched]

    # ------------------------------------------------------------------ run loop

    def run(self):
        self._validate_environment()
        self.out_dir.mkdir(parents=True, exist_ok=True)
        LOG.info("%d job(s): %d mask attack, %d dictionary.", len(self.jobs), len(self.mask_jobs), len(self.dict_jobs))

        if self.mask_job_groups:
            LOG.info("Running %d mask job(s)...", len(self.mask_jobs))
            result = self._run_job_groups(self.mask_job_groups, wl=None)
            if result:
                self._handle_found(None, result)
                return
            LOG.info("Mask jobs exhausted — no password found.")

        if not self.dict_job_groups:
            LOG.info("No dictionary jobs configured; done.")
            return
        LOG.info("Watching %s for %s_*.txt — %d dictionary job(s) across %d device group(s), every %.1fs.",
                 self.dict_dir, self.stem, len(self.dict_jobs), len(self.dict_job_groups), self.poll_interval)
        while not self._shutdown:
            pending = self._pending_wordlists()
            if not pending:
                time.sleep(self.poll_interval)
                continue
            for wl in pending:
                if self._shutdown:
                    return
                if not wl.exists():  # raced with a rename; skip
                    continue
                LOG.info("Testing %s ...", wl.name)
                result = self._run_job_groups(self.dict_job_groups, wl=wl)
                if result:
                    self._handle_found(wl, result)
                    return  # success → stop
                self._mark_done(wl)

    def _mark_done(self, wl):
        target = wl.with_name("DONE_" + wl.name)
        try:
            os.replace(wl, target)  # atomic on the same filesystem, overwrites on Windows too
            LOG.info("No password in %s - marked %s.", wl.name, target.name)
        except OSError as e:
            LOG.error("Could not rename %s: %s", wl.name, e)

    def _handle_found(self, wl, result):
        pw, job = result["password"], result["job"]
        dev = job["device"]
        wl_desc = wl.name if wl is not None else f"mask({job.get('mask', '?')})"
        LOG.info("PASSWORD FOUND in %s (mode %s%s).", wl_desc, job["mode"],
                 f", device {dev}" if dev is not None else "")
        stamp = datetime.now().strftime("%Y/%m/%d %H:%M:%S")
        record = f"[{stamp}] source={wl_desc} mode={job['mode']} device={dev} password={pw}\n"
        try:
            with open(self.found_file, "a", encoding="utf-8") as f:
                f.write(record)
            LOG.info("Recorded result in %s.", self.found_file)
        except OSError as e:
            LOG.error("Could not write %s: %s", self.found_file, e)
        print(pw)  # clean stdout: just the password, for capture/piping
        sys.stdout.flush()
        self.shutdown()

    # ------------------------------------------------------------------ hashcat

    def _run_job_groups(self, job_groups, wl):  # run a set of device-grouped jobs; wl=None for mask attacks
        result = {"password": None, "job": None}
        found_event = threading.Event()
        lock = threading.Lock()
        threads = []
        for jobs in job_groups.values():
            t = threading.Thread(target=self._device_worker,
                                 args=(jobs, wl, result, found_event, lock),
                                 daemon=True)
            t.start()
            threads.append(t)
        for t in threads:
            t.join()
        return result if found_event.is_set() else None

    def _device_worker(self, jobs, wl, result, found_event, lock):
        for job in jobs:
            if found_event.is_set() or self._shutdown:
                return
            pw = self._run_job(job, wl, found_event)
            if pw is not None:
                with lock:
                    if not found_event.is_set():
                        result["password"] = pw
                        result["job"] = job
                        found_event.set()
                return

    def _build_cmd(self, job, wl, outfile, tag):
        attack_mode = job["attack_mode"]
        cmd = [self.hcat_path,
               "-m", str(job["mode"]),
               "-a", str(attack_mode),
               "--quiet",
               "--potfile-disable",               # always actually run; no stale short-circuit
               "--restore-disable",               # parallel instances must not share restore state
               "--session", f"passtest_{tag}",    # unique session per concurrent instance
               "--outfile-format", "2",           # plain password only (avoids ':' ambiguity)
               "-o", str(outfile)]
        if job["device"] is not None:
            cmd += ["-d", str(job["device"])]
        for rule in job.get("rules", []):
            cmd += ["-r", rule]
        cmd += job["args"]
        cmd += self.extra_global
        cmd.append(str(self.header_bin))
        cmd.append(job["mask"] if attack_mode == 3 else str(wl))
        return cmd

    def _run_job(self, job, wl, found_event):
        wl_stem = wl.stem if wl is not None else "mask"
        tag = re.sub(r"[^A-Za-z0-9_]", "_", f"{job['device']}_{job['mode']}_{wl_stem}")
        outfile = self.out_dir / f"cracked_{tag}.txt"
        errfile = self.out_dir / f"hcat_{tag}.log"
        try:
            if outfile.exists():
                outfile.unlink()
        except OSError:
            pass
        cmd = self._build_cmd(job, wl, outfile, tag)
        wl_desc = wl.name if wl is not None else job.get("mask", "mask")
        LOG.info("hashcat -m %s -a %s%s on %s", job["mode"], job["attack_mode"],
                 f" -d {job['device']}" if job["device"] is not None else "", wl_desc)
        LOG.debug("command: %s", " ".join(cmd))
        try:
            err_fh = open(errfile, "w", encoding="utf-8", errors="replace")
        except OSError as e:
            LOG.error("Could not open log %s: %s", errfile, e)
            err_fh = subprocess.DEVNULL
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=err_fh)
        except OSError as e:
            LOG.error("Failed to launch hashcat (%s): %s", self.hcat_path, e)
            if err_fh not in (subprocess.DEVNULL, None):
                err_fh.close()
            return None
        with self._procs_lock:
            self._active_procs.add(proc)
        try:
            while True:
                try:
                    proc.wait(timeout=1.0)
                    break
                except subprocess.TimeoutExpired:
                    if found_event.is_set() or self._shutdown:
                        self._terminate(proc)  # another job already won, or we're shutting down
                        return None
        finally:
            with self._procs_lock:
                self._active_procs.discard(proc)
            if err_fh not in (subprocess.DEVNULL, None):
                err_fh.close()

        rc = proc.returncode
        pw = self._read_password(outfile)
        if pw is not None:
            return pw
        if rc == 1:
            LOG.info("Exhausted: mode %s found nothing in %s.", job["mode"], wl.name)
        elif rc == 0:
            LOG.info("mode %s on %s finished with no password captured.", job["mode"], wl.name)
        else:
            LOG.warning("hashcat mode %s exited with code %s%s.", job["mode"], rc,
                        self._tail_hint(errfile))
        return None

    @staticmethod
    def _read_password(outfile):
        try:
            if outfile.exists() and outfile.stat().st_size > 0:
                with open(outfile, "r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        line = line.rstrip("\r\n")
                        if line:
                            return line
        except OSError as e:
            LOG.warning("Could not read outfile %s: %s", outfile, e)
        return None

    @staticmethod
    def _tail_hint(errfile):  # short hint from hashcat's stderr for an unexpected exit
        try:
            text = Path(errfile).read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            return ""
        if not text:
            return ""
        last = text.splitlines()[-1].strip()
        return f" - {last[:200]} (see {errfile})"

    def _terminate(self, proc):
        try:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        except OSError:
            pass

    def shutdown(self):
        self._shutdown = True
        with self._procs_lock:
            procs = list(self._active_procs)
        for p in procs:
            self._terminate(p)


def load_config(path):
    try:
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        LOG.error("Could not read configuration %s: %s", path, e)
        sys.exit(1)
    if not isinstance(cfg, dict):
        LOG.error("Configuration %s is not a JSON object.", path)
        sys.exit(1)
    missing = [k for k in REQUIRED_KEYS if k not in cfg]
    if missing:
        LOG.error("Configuration %s missing required key(s): %s", path, ", ".join(missing))
        sys.exit(1)
    return cfg


def main():
    parser = argparse.ArgumentParser(
        description="Watch a directory for passgen wordlist chunks and crack a "
                    "TrueCrypt/VeraCrypt header with hashcat.")
    parser.add_argument("-c", "--config", default=None,
                        help="Path to passtest.json (default: next to this script).")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Verbose logging (prints each hashcat command line).")
    args = parser.parse_args()
    setup_logging(args.verbose)

    config_path = (Path(args.config) if args.config
                   else Path(__file__).resolve().parent / DEFAULT_CONFIG_NAME)
    if not config_path.is_file():
        LOG.error("Configuration file not found: %s", config_path)
        sys.exit(1)

    cfg = load_config(config_path)
    tester = Passtest(cfg, config_path.resolve().parent)
    try:
        tester.run()
    except KeyboardInterrupt:
        LOG.info("Interrupted — shutting down hashcat instances.")
        tester.shutdown()
        sys.exit(130)


if __name__ == "__main__":
    main()
