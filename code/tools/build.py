"""Repeatable local build: compile, lint, test, wheel, install and installed-wheel demo."""

import argparse
import json
import os
import platform
import sqlite3
import subprocess
import sys
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from metrics import measure


def main():
    """Persist raw command output and a machine-readable success/failure manifest."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ruff-path", type=Path, help="optional local pip --target tool directory")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    evidence = root.parent / "evidence"
    evidence.mkdir(exist_ok=True)
    scratch = root / ".build-tmp" / uuid4().hex
    scratch.mkdir(parents=True)
    env = dict(os.environ)
    env.update(
        {
            "PYTHONIOENCODING": "utf-8",
            "PYTHONPATH": str(root / "src"),
            "TEMP": str(scratch),
            "TMP": str(scratch),
        }
    )
    lint_env = dict(env)
    if args.ruff_path:
        lint_env["PYTHONPATH"] = str(args.ruff_path.resolve())
    manifest = {
        "started_at_utc": datetime.now(UTC).isoformat(),
        "python": sys.version,
        "platform": platform.platform(),
        "sqlite": sqlite3.sqlite_version,
        "stages": [],
    }

    def run(name, command, environment=env):
        result = subprocess.run(
            command,
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        output = result.stdout + result.stderr
        (evidence / f"{name}.txt").write_text(
            "$ " + subprocess.list2cmdline(command) + "\n" + output, encoding="utf-8"
        )
        manifest["stages"].append({"name": name, "exit_code": result.returncode})
        (evidence / "build.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(name, "PASS" if result.returncode == 0 else "FAIL", flush=True)
        if result.returncode:
            print(output)
            raise SystemExit(result.returncode)
        return result.stdout

    run("01_compile", [sys.executable, "-m", "compileall", "-q", "src"])
    run("02_ruff_version", [sys.executable, "-m", "ruff", "--version"], lint_env)
    run("03_lint", [sys.executable, "-m", "ruff", "check", "src", "tests", "tools"], lint_env)
    run(
        "04_format",
        [sys.executable, "-m", "ruff", "format", "--check", "src", "tests", "tools"],
        lint_env,
    )
    run("05_tests", [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"])
    run(
        "06_wheel",
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-index",
            "--no-deps",
            "--no-build-isolation",
            ".",
            "--wheel-dir",
            "dist",
        ],
    )
    version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    wheel = next((root / "dist").glob(f"campus_market_lab3-{version}-*.whl"))
    site = scratch / "installed"
    run(
        "07_install",
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-index",
            "--no-deps",
            "--target",
            str(site),
            str(wheel),
        ],
    )
    # -I ignores PYTHONPATH; the demo must use the package installed from the wheel.
    script = (
        "import sys;sys.path.insert(0,sys.argv.pop(1));"
        "import campus_market;print('INSTALLED_PACKAGE',campus_market.__file__);"
        "from campus_market.cli import main;raise SystemExit(main())"
    )
    run(
        "08_installed_demo",
        [
            sys.executable,
            "-X",
            "utf8",
            "-I",
            "-c",
            script,
            str(site),
            "--db",
            str(scratch / "installed-demo.db"),
            "demo",
        ],
    )
    metrics = measure(root)
    (evidence / "source_metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    if metrics["conservative_token_line_total"] <= 500:
        raise SystemExit("Application-only conservative source count must exceed 500")
    manifest.update(
        {
            "source_metrics": metrics,
            "wheel": wheel.name,
            "finished_at_utc": datetime.now(UTC).isoformat(),
            "success": True,
        }
    )
    (evidence / "build.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print("BUILD COMPLETE", wheel.name, flush=True)


if __name__ == "__main__":
    main()
