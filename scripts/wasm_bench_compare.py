#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

BENCH_CMD = [
    "cargo",
    "bench",
    "--target=wasm32-wasip1",
    "--",
    "-Z",
    "unstable-options",
    "--format",
    "json",
]
NATIVE_BENCH_CMD = [
    "cargo",
    "bench",
    "--",
    "-Z",
    "unstable-options",
    "--format",
    "json",
]
SIMD_FLAGS = "-Ctarget-feature=+simd128"


def run_bench(
    label: str,
    out_dir: Path,
    runs: int,
    rustflags: str | None,
    bench_cmd: list[str] | None = None,
) -> list[Path]:
    out_files: list[Path] = []
    cmd = bench_cmd if bench_cmd is not None else BENCH_CMD
    for idx in range(1, runs + 1):
        out_file = out_dir / f"{label}-{idx}.json"
        env = os.environ.copy()
        if rustflags is not None:
            env["RUSTFLAGS"] = rustflags
        else:
            env.pop("RUSTFLAGS", None)

        print(f"Running {label} benchmark {idx}/{runs} -> {out_file}")
        with out_file.open("w", encoding="utf-8") as stdout_fh:
            proc = subprocess.run(
                cmd,
                env=env,
                stdout=stdout_fh,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
        if proc.returncode != 0:
            raise RuntimeError(
                f"Benchmark run failed ({label} #{idx}) with exit code {proc.returncode}\n"
                f"stderr:\n{proc.stderr}"
            )
        out_files.append(out_file)
    return out_files


def parse_bench_file(path: Path) -> dict[str, float]:
    values: dict[str, float] = {}
    with path.open("r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if item.get("type") != "bench":
                continue

            name = item.get("name")
            if not isinstance(name, str):
                continue

            value = item.get("mean")
            if value is None:
                value = item.get("median")
            if not isinstance(value, (int, float)):
                print(
                    f"Skipping non-numeric result in {path}:{line_no} for benchmark {name!r}",
                    file=sys.stderr,
                )
                continue
            values[name] = float(value)
    return values


def aggregate_mean(paths: list[Path]) -> dict[str, float]:
    by_test: dict[str, list[float]] = defaultdict(list)
    for path in paths:
        parsed = parse_bench_file(path)
        for test_name, value in parsed.items():
            by_test[test_name].append(value)
    return {name: sum(vals) / len(vals) for name, vals in by_test.items() if vals}


def print_comparison(base: dict[str, float], simd: dict[str, float], native: dict[str, float]) -> None:
    tests = sorted(set(base) | set(simd) | set(native))
    simd_ratios: list[float] = []
    simd_deltas: list[float] = []
    native_ratios: list[float] = []
    native_deltas: list[float] = []
    print(
        f"{'test':<34} {'base_mean':>12} {'simd_mean':>12} {'native_mean':>12} "
        f"{'simd/base':>10} {'simd_d%':>9} {'native/base':>12} {'native_d%':>10}"
    )
    print("-" * 125)
    for test in tests:
        b = base.get(test)
        s = simd.get(test)
        n = native.get(test)

        b_str = f"{b:.3f}" if b is not None else "n/a"
        s_str = f"{s:.3f}" if s is not None else "n/a"
        n_str = f"{n:.3f}" if n is not None else "n/a"

        if b is None or b == 0:
            print(
                f"{test:<34} {b_str:>12} {s_str:>12} {n_str:>12} "
                f"{'n/a':>10} {'n/a':>9} {'n/a':>12} {'n/a':>10}"
            )
            continue

        simd_ratio_str = "n/a"
        simd_delta_str = "n/a"
        if s is not None:
            ratio = s / b
            delta = (ratio - 1.0) * 100.0
            simd_ratios.append(ratio)
            simd_deltas.append(delta)
            simd_ratio_str = f"{ratio:.3f}"
            simd_delta_str = f"{delta:.2f}"

        native_ratio_str = "n/a"
        native_delta_str = "n/a"
        if n is not None:
            ratio = n / b
            delta = (ratio - 1.0) * 100.0
            native_ratios.append(ratio)
            native_deltas.append(delta)
            native_ratio_str = f"{ratio:.3f}"
            native_delta_str = f"{delta:.2f}"

        print(
            f"{test:<34} {b:12.3f} {s_str:>12} {n_str:>12} "
            f"{simd_ratio_str:>10} {simd_delta_str:>9} {native_ratio_str:>12} {native_delta_str:>10}"
        )

    print("-" * 125)
    if simd_ratios or native_ratios:
        simd_mean_ratio = sum(simd_ratios) / len(simd_ratios) if simd_ratios else None
        simd_mean_delta = sum(simd_deltas) / len(simd_deltas) if simd_deltas else None
        native_mean_ratio = sum(native_ratios) / len(native_ratios) if native_ratios else None
        native_mean_delta = sum(native_deltas) / len(native_deltas) if native_deltas else None
        simd_ratio_str = f"{simd_mean_ratio:.3f}" if simd_mean_ratio is not None else "n/a"
        simd_delta_str = f"{simd_mean_delta:.2f}" if simd_mean_delta is not None else "n/a"
        native_ratio_str = f"{native_mean_ratio:.3f}" if native_mean_ratio is not None else "n/a"
        native_delta_str = f"{native_mean_delta:.2f}" if native_mean_delta is not None else "n/a"
        print(
            f"{'MEAN (all comparable tests)':<34} {'':>12} {'':>12} {'':>12} "
            f"{simd_ratio_str:>10} {simd_delta_str:>9} {native_ratio_str:>12} {native_delta_str:>10}"
        )
    else:
        print("MEAN (all comparable tests): n/a")


def collect_files(out_dir: Path, label: str, runs: int) -> list[Path]:
    return [out_dir / f"{label}-{idx}.json" for idx in range(1, runs + 1)]


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run wasm benches for baseline and SIMD, collect JSON output, and "
            "print per-test comparison using mean values across runs."
        )
    )
    parser.add_argument("--runs", type=int, default=1, help="Number of runs per variant")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("bench-json"),
        help="Directory where raw JSON outputs are stored",
    )
    parser.add_argument(
        "--reuse",
        action="store_true",
        help="Reuse existing JSON files in out-dir instead of running benchmarks",
    )
    args = parser.parse_args()

    if args.runs < 1:
        print("--runs must be >= 1", file=sys.stderr)
        return 2

    args.out_dir.mkdir(parents=True, exist_ok=True)

    try:
        if args.reuse:
            base_files = collect_files(args.out_dir, "baseline", args.runs)
            simd_files = collect_files(args.out_dir, "simd128", args.runs)
            native_files = collect_files(args.out_dir, "native", args.runs)
            missing = [str(p) for p in base_files + simd_files + native_files if not p.exists()]
            if missing:
                raise FileNotFoundError(
                    "Missing expected JSON files for --reuse:\n" + "\n".join(missing)
                )
        else:
            base_files = run_bench("baseline", args.out_dir, args.runs, rustflags=None)
            simd_files = run_bench("simd128", args.out_dir, args.runs, rustflags=SIMD_FLAGS)
            native_files = run_bench(
                "native",
                args.out_dir,
                args.runs,
                rustflags=None,
                bench_cmd=NATIVE_BENCH_CMD,
            )

        base_mean = aggregate_mean(base_files)
        simd_mean = aggregate_mean(simd_files)
        native_mean = aggregate_mean(native_files)
        print_comparison(base_mean, simd_mean, native_mean)
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
