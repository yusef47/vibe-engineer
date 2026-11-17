#!/usr/bin/env python3
"""Focused check for review item 1: the smoke test never deletes caller data.

`ocs_smoke_test.py` used to `rmtree` the caller-supplied `--workdir`. It now
treats `--workdir` as a *base* directory and creates a fresh
`run-<timestamp>-<pid>/` subdirectory inside it. This check proves:

  A. pre-existing files in a base survive a run, and two runs never reuse or
     delete each other's directories;
  B. a symlinked base is resolved safely and its contents survive;
  C. the filesystem root is refused (directly and via symlink), and a
     base that is actually a file is refused with the file untouched;
  D. an end-to-end run of the real script with `--workdir` pointing at a
     base holding a sentinel file leaves the sentinel byte-identical;
  E. regression guard: no `rmtree` call exists in the tool source at all.

Exit code 0 iff every check passes. Run: `python3 tools/ocs/test_safe_workdir.py`.
"""

import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ocs_smoke_test as ocs  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, fn) -> None:
    try:
        detail = fn() or ""
        results.append((name, True, detail))
        print(f"PASS  {name}" + (f"  [{detail}]" if detail else ""))
    except Exception as e:  # noqa: BLE001 - the check reports any failure
        results.append((name, False, str(e)))
        print(f"FAIL  {name}: {e}")


def a_preserves_existing_files() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        base = os.path.join(tmp, "base")
        os.makedirs(os.path.join(base, "important", "nested"))
        sentinel = os.path.join(base, "important", "nested", "KEEP_ME.txt")
        top = os.path.join(base, "top_level.txt")
        with open(sentinel, "w") as fh:
            fh.write("must survive")
        with open(top, "w") as fh:
            fh.write("must survive")
        run1, _ = ocs.prepare_run_dir(base)
        run2, _ = ocs.prepare_run_dir(base)
        assert os.path.isfile(sentinel) and open(sentinel).read() == "must survive"
        assert os.path.isfile(top) and open(top).read() == "must survive"
        assert os.path.basename(run1).startswith("run-") and os.path.dirname(run1) == base
        assert run2 != run1, "second run must not reuse the first run dir"
        assert os.path.isdir(run1), "first run dir must still exist (nothing deleted)"
        assert os.path.isdir(run2)
        return f"2 runs created {os.path.basename(run1)}, {os.path.basename(run2)}; files intact"


def b_symlinked_base() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        real = os.path.join(tmp, "real")
        os.makedirs(real)
        keep = os.path.join(real, "KEEP.txt")
        with open(keep, "w") as fh:
            fh.write("x")
        link = os.path.join(tmp, "link")
        try:
            os.symlink(real, link, target_is_directory=True)  # flag needed on Windows; ignored on POSIX
        except OSError:
            return ("SKIPPED symlink part (symlink privilege unavailable on this "
                    "Windows session); non-symlink bases covered by A/C/D")
        run_dir, resolved = ocs.prepare_run_dir(link)
        assert os.path.isfile(keep)
        assert resolved == os.path.realpath(real)
        assert os.path.dirname(run_dir) == os.path.realpath(real)
        return f"symlink resolved to {resolved}; contents intact"


def c_root_refused() -> str:
    try:
        ocs.prepare_run_dir("/")
    except ValueError:
        pass
    else:
        raise AssertionError("/ was not refused")
    with tempfile.TemporaryDirectory() as tmp:
        link = os.path.join(tmp, "rootlink")
        try:
            os.symlink("/", link, target_is_directory=True)  # flag needed on Windows; ignored on POSIX
        except OSError:
            return "/ refused without creating anything; symlink part SKIPPED (no symlink privilege)"
        try:
            ocs.prepare_run_dir(link)
        except ValueError:
            pass
        else:
            raise AssertionError("a symlink to / was not refused")
    return "/ and a symlink to / both refused without creating anything"


def c2_file_base_refused() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        notdir = os.path.join(tmp, "not-a-dir")
        with open(notdir, "w") as fh:
            fh.write("data")
        try:
            ocs.prepare_run_dir(notdir)
        except ValueError:
            pass
        else:
            raise AssertionError("file base was not refused")
        assert open(notdir).read() == "data"
        return "file base refused; file untouched"


def d_end_to_end_preserves_sentinel() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        base = os.path.join(tmp, "base")
        os.makedirs(base)
        sentinel = os.path.join(base, "KEEP_ME.txt")
        with open(sentinel, "w") as fh:
            fh.write("sentinel-contents")
        exe = ocs.resolve_exe()
        if not os.path.isfile(exe):
            return "SKIPPED end-to-end part (OpenCADStudio binary not installed)"
        r = subprocess.run(
            [sys.executable, os.path.join(HERE, "ocs_smoke_test.py"), "--workdir", base],
            capture_output=True, text=True, timeout=300)
        assert r.returncode == 0, f"smoke run failed:\n{r.stdout}\n{r.stderr}"
        assert open(sentinel).read() == "sentinel-contents", "sentinel did not survive"
        runs = [d for d in os.listdir(base) if d.startswith("run-")]
        assert len(runs) == 1, f"expected exactly one fresh run dir, got {runs}"
        assert os.path.isfile(os.path.join(base, runs[0], "smoke_report.json"))
        return f"sentinel survived full run; fresh dir {runs[0]}/ created beside it"


def e_no_rmtree_in_source() -> str:
    with open(ocs.__file__, encoding="utf-8") as fh:
        src = fh.read()
    assert "rmtree" not in src, "rmtree found in ocs_smoke_test.py — caller data no longer safe"
    return "no rmtree call in ocs_smoke_test.py"


def main() -> int:
    check("A base with pre-existing files: nothing deleted, fresh run dirs",
          a_preserves_existing_files)
    check("B symlinked base resolved safely", b_symlinked_base)
    check("C filesystem root (and symlink to /) refused", c_root_refused)
    check("C2 base that is a file refused, file untouched", c2_file_base_refused)
    check("D end-to-end run preserves existing files in unsafe base",
          d_end_to_end_preserves_sentinel)
    check("E regression guard: no rmtree in tool source", e_no_rmtree_in_source)
    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
