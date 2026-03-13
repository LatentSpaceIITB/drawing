#!/usr/bin/env python3
"""
run_pipeline.py  --  Master runner for the P&ID latent-space pipeline.

Orchestrates stages 1-10 sequentially via subprocess, with support for
single-file or batch (directory) mode, partial stage ranges, and dry-run.
"""

import argparse
import os
import sys
import subprocess
import time
import glob as globmod


# ---------------------------------------------------------------------------
# Stage definitions
# ---------------------------------------------------------------------------
# Each entry: (label, script, builder_func)
# builder_func(src_dir, out_dir) -> list[str] of CLI args (without python/script)

def _stage_1(src, out):
    return ["--input", os.path.join(out, "input.graphml"),
            "--output", os.path.join(out, "ground_truth.json")]

def _stage_2(src, out):
    return ["--input", os.path.join(out, "ground_truth.json"),
            "--output", os.path.join(out, "fragments.json")]

def _stage_3a(src, out):
    return ["--input", os.path.join(out, "fragments.json"),
            "--output", os.path.join(out, "canonical_descriptions.json")]

def _stage_3b(src, out):
    return ["--input", os.path.join(out, "canonical_descriptions.json"),
            "--output", os.path.join(out, "text_variants.json")]

def _stage_3c(src, out):
    return ["--input", os.path.join(out, "fragments.json"),
            "--output", os.path.join(out, "pfd_macros.json")]

def _stage_4(src, out):
    return ["--input-macros", os.path.join(out, "pfd_macros.json"),
            "--input-fragments", os.path.join(out, "fragments.json"),
            "--input-table", os.path.join(src, "expansion_table.json"),
            "--output", os.path.join(out, "pfd_layout_primitives.json")]

def _stage_5(src, out):
    return ["--input-primitives", os.path.join(out, "pfd_layout_primitives.json"),
            "--input-fragments", os.path.join(out, "fragments.json"),
            "--output", os.path.join(out, "pfd_global_graph.json")]

def _stage_6(src, out):
    return ["--input", os.path.join(out, "pfd_global_graph.json"),
            "--input-fragments", os.path.join(out, "fragments.json"),
            "--output", os.path.join(out, "pfd_layout_realized.json")]

def _stage_7(src, out):
    return ["--input-layout", os.path.join(out, "pfd_layout_realized.json"),
            "--input-graph", os.path.join(out, "pfd_global_graph.json"),
            "--output", os.path.join(out, "pfd_interaction_hooks.json")]

def _stage_8(src, out):
    return ["--input", os.path.join(out, "pfd_global_graph.json"),
            "--rules", os.path.join(src, "pid_expansion_rules.json"),
            "--output", os.path.join(out, "pid_primitives.json")]

def _stage_9(src, out):
    return ["--input-primitives", os.path.join(out, "pid_primitives.json"),
            "--input-layout", os.path.join(out, "pfd_layout_realized.json"),
            "--output-graph", os.path.join(out, "pid_global_graph.json"),
            "--output-layout", os.path.join(out, "pid_layout_realized.json"),
            "--output-hooks", os.path.join(out, "pid_interaction_hooks.json")]

def _stage_10(src, out):
    return ["--input-layout", os.path.join(out, "pid_layout_realized.json"),
            "--input-hooks", os.path.join(out, "pid_interaction_hooks.json"),
            "--input-symbols", os.path.join(src, "pid_symbol_library.json"),
            "--output", os.path.join(out, "pid.svg")]


STAGES = [
    ("1",   "Stage 1:  Feature extraction",        "extract_features.py",   _stage_1),
    ("2",   "Stage 2:  Fragment generation",        "generate_fragments.py", _stage_2),
    ("3a",  "Stage 3a: Canonical descriptions",     "generate_stage3a.py",   _stage_3a),
    ("3b",  "Stage 3b: Linguistic variants (LLM)",  "generate_stage3b.py",   _stage_3b),
    ("3c",  "Stage 3c: PFD macro mapping",          "generate_stage3c.py",   _stage_3c),
    ("4",   "Stage 4:  Layout primitive expansion",  "generate_stage4.py",   _stage_4),
    ("5",   "Stage 5:  Global graph assembly",       "generate_stage5.py",   _stage_5),
    ("6",   "Stage 6:  PFD layout realization",      "generate_stage6.py",   _stage_6),
    ("7",   "Stage 7:  PFD interaction hooks",       "generate_stage7.py",   _stage_7),
    ("8",   "Stage 8:  P&ID expansion rules",        "generate_stage8.py",   _stage_8),
    ("9",   "Stage 9:  P&ID graph + layout + hooks", "generate_stage9.py",   _stage_9),
    ("10",  "Stage 10: SVG rendering",               "generate_stage10.py",  _stage_10),
]

# Ordered numeric keys for range filtering (3a/3b/3c map to 3)
_STAGE_ORDER = {
    "1": 1, "2": 2, "3a": 3, "3b": 3, "3c": 3,
    "4": 4, "5": 5, "6": 6, "7": 7, "8": 8, "9": 9, "10": 10,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _parse_stage_range(spec: str) -> tuple[int, int]:
    """Parse a stage range like '1-6' or '3-10' into (lo, hi) inclusive."""
    parts = spec.split("-")
    if len(parts) == 1:
        v = int(parts[0])
        return (v, v)
    if len(parts) == 2:
        return (int(parts[0]), int(parts[1]))
    raise ValueError(f"Invalid stage range: {spec!r}")


def _in_range(stage_key: str, lo: int, hi: int) -> bool:
    num = _STAGE_ORDER[stage_key]
    return lo <= num <= hi


def _copy_graphml(graphml_path: str, out_dir: str) -> None:
    """Copy (or symlink) the input graphml into the output dir as input.graphml."""
    dest = os.path.join(out_dir, "input.graphml")
    src = os.path.abspath(graphml_path)
    if os.path.exists(dest):
        os.remove(dest)
    # Use a symlink for efficiency; fall back to copy
    try:
        os.symlink(src, dest)
    except OSError:
        import shutil
        shutil.copy2(src, dest)


# ---------------------------------------------------------------------------
# Pipeline runner (single file)
# ---------------------------------------------------------------------------

def run_pipeline(graphml_path: str, output_dir: str,
                 src_dir: str, stage_range: str,
                 dry_run: bool) -> int:
    """
    Run the pipeline for a single GraphML file.
    Returns 0 on full success, 1 on any failure.
    """
    lo, hi = _parse_stage_range(stage_range)
    os.makedirs(output_dir, exist_ok=True)

    # Place the input graphml into the output dir so stage 1 can find it
    _copy_graphml(graphml_path, output_dir)

    successes = 0
    failures = 0
    skipped = 0
    failed_stages: list[str] = []

    for stage_key, label, script, arg_builder in STAGES:
        if not _in_range(stage_key, lo, hi):
            continue

        # Skip 3b if no API key
        if stage_key == "3b" and not os.environ.get("ANTHROPIC_API_KEY"):
            _log(f"  SKIP  {label}  (ANTHROPIC_API_KEY not set)")
            skipped += 1
            continue

        script_path = os.path.join(src_dir, script)
        args = arg_builder(src_dir, output_dir)
        cmd = [sys.executable, script_path] + args

        if dry_run:
            _log(f"  [dry-run] {' '.join(cmd)}")
            continue

        _log(f"  RUN   {label}")
        t0 = time.monotonic()
        result = subprocess.run(cmd, cwd=src_dir)
        elapsed = time.monotonic() - t0

        if result.returncode == 0:
            _log(f"  OK    {label}  ({elapsed:.1f}s)")
            successes += 1
        else:
            _log(f"  FAIL  {label}  (exit {result.returncode}, {elapsed:.1f}s)")
            failures += 1
            failed_stages.append(label)
            # Stop on first failure — later stages depend on earlier outputs
            break

    if dry_run:
        return 0

    _log("")
    _log(f"  Summary: {successes} ok, {failures} failed, {skipped} skipped")
    if failed_stages:
        _log(f"  Failed: {', '.join(failed_stages)}")
    return 1 if failures else 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the P&ID latent-space pipeline (stages 1-10).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  %(prog)s --graphml 0.graphml
  %(prog)s --graphml 0.graphml --output-dir out/my_run --stages 1-6
  %(prog)s --graphml-dir ./graphml --output-dir out/
  %(prog)s --graphml 0.graphml --dry-run
""",
    )
    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument("--graphml", metavar="PATH",
                     help="Single GraphML file to process")
    grp.add_argument("--graphml-dir", metavar="DIR",
                     help="Directory of GraphML files (batch mode)")
    parser.add_argument("--output-dir", metavar="DIR", default=None,
                        help="Output directory (default: out/<basename>/)")
    parser.add_argument("--stages", default="1-10",
                        help="Stage range to run, e.g. '1-6' (default: 1-10)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show commands without executing")
    args = parser.parse_args()

    src_dir = os.path.dirname(os.path.abspath(__file__))

    # Collect (graphml_path, output_dir) pairs
    jobs: list[tuple[str, str]] = []

    if args.graphml:
        gpath = os.path.abspath(args.graphml)
        if not os.path.isfile(gpath):
            _log(f"Error: file not found: {gpath}")
            return 1
        basename = os.path.splitext(os.path.basename(gpath))[0]
        out = args.output_dir if args.output_dir else os.path.join("out", basename)
        jobs.append((gpath, os.path.abspath(out)))
    else:
        gdir = os.path.abspath(args.graphml_dir)
        if not os.path.isdir(gdir):
            _log(f"Error: directory not found: {gdir}")
            return 1
        files = sorted(globmod.glob(os.path.join(gdir, "*.graphml")))
        if not files:
            _log(f"Error: no .graphml files found in {gdir}")
            return 1
        base_out = args.output_dir if args.output_dir else "out"
        base_out = os.path.abspath(base_out)
        for f in files:
            basename = os.path.splitext(os.path.basename(f))[0]
            jobs.append((f, os.path.join(base_out, basename)))

    _log(f"Pipeline: {len(jobs)} job(s), stages {args.stages}"
         f"{' (dry-run)' if args.dry_run else ''}")
    _log("")

    total_failures = 0
    for i, (gpath, out_dir) in enumerate(jobs, 1):
        _log(f"[{i}/{len(jobs)}] {os.path.basename(gpath)} -> {out_dir}")
        rc = run_pipeline(gpath, out_dir, src_dir, args.stages, args.dry_run)
        if rc != 0:
            total_failures += 1
        _log("")

    if len(jobs) > 1:
        _log(f"Batch complete: {len(jobs) - total_failures}/{len(jobs)} succeeded")

    return 1 if total_failures else 0


if __name__ == "__main__":
    sys.exit(main())
