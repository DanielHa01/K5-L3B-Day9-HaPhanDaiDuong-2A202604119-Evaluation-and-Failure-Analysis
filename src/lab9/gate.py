"""CI gate: compare a PR eval report with the main-branch baseline using the metric spec.

    python -m lab9.gate --pr reports/eval_workflow.json --baseline reports/baseline_v1.json

exit 0 = merge, exit 1 = blocked. Safety metrics: max 0, no averaging away.
Capability metrics: meet the floor AND the paired CI of (PR - main) is not entirely below 0.
Thresholds may only get stricter on the same eval-set version (ratchet).
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from lab8.console import utf8_stdio
from lab9 import stats
from lab9.eval import SPEC, aggregate, load_spec, per_item
from lab9.graders import GRADERS


def integrity_check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> list[str]:
    """M7 (Expert): refuse to compare reports that cannot be trusted. Return a list of problems
    (empty = trustworthy). A gate that passes a broken report is worse than no gate.
    Must catch at least:
      - any metric value in pr["metrics"] that is NaN or inf
      - len(pr["rows"]) != manifest items x len(manifest seeds)   (dropped / duplicated rows)
      - the (item_id, seed) set of PR differs from the baseline's (cherry-picked seeds/items)
      - pr["evaluator_failures"] non-empty, or a row whose value for a graded spec metric is
        None (a crashed grader silently scores as "missing" and drops out of the mean)
      - base_spec given and a threshold in spec is LOOSER than in base_spec for the same
        eval-set version (ratchet: max may not rise, min may not fall, a blocking metric may
        not become non-blocking, a metric may not disappear; the same for spec["slices"],
        including dropping no_significant_drop)
      - an item's slice tags in PR rows differ from the baseline's (relabelling an item out of a
        slice hides its regression from the slice gate)"""
    problems = []
    graded = {m["id"] for m in spec["metrics"] if m["id"] in GRADERS}
    has_cost = any(m["id"] == "cost_per_success" for m in spec["metrics"])
    keys_by_report = []
    slices_by_report = []
    for name, report in (("PR", pr), ("baseline", base)):
        rows, manifest = report.get("rows", []), report.get("manifest", {})
        seeds = manifest.get("seeds", [])
        item_count = manifest.get("items", 0)
        keys = [(r.get("item_id"), r.get("seed")) for r in rows]
        key_set = set(keys)
        keys_by_report.append(key_set)
        items = {item for item, _ in keys}
        expected = {(item, seed) for item in items for seed in seeds}
        if (not rows or not seeds or len(seeds) != len(set(seeds))
                or len(items) != item_count or len(rows) != item_count * len(seeds)
                or len(keys) != len(key_set) or key_set != expected):
            problems.append(f"{name}: rows do not form the manifest's complete unique item × seed grid")
        if report.get("evaluator_failures"):
            problems.append(f"{name}: evaluator_failures is non-empty")
        for mid, value in report.get("metrics", {}).items():
            if not _finite(value):
                problems.append(f"{name}: metric {mid} is missing or nonfinite")
        for m in spec["metrics"]:
            if m["id"] not in report.get("metrics", {}):
                problems.append(f"{name}: missing metric {m['id']}")
        slices = {}
        for row in rows:
            item = row.get("item_id")
            tags = frozenset(row.get("slices") or [])
            if item in slices and slices[item] != tags:
                problems.append(f"{name}: inconsistent slice tags for {item}")
            slices[item] = tags
            for mid in graded:
                if not _finite(row.get(mid)) or row[mid] not in (0, 1):
                    problems.append(f"{name}: {item}@{row.get('seed')} invalid graded metric {mid} (expected 0/1)")
            if has_cost and (not _finite(row.get("cost")) or row["cost"] < 0):
                problems.append(f"{name}: {item}@{row.get('seed')} invalid cost")
        slices_by_report.append(slices)
        if (rows and all(_finite(r.get(mid)) and r[mid] in (0, 1) for r in rows for mid in graded)
                and all(_finite(r.get("cost", 0)) and r.get("cost", 0) >= 0 for r in rows)):
            recomputed = aggregate([{**r, "cost": r.get("cost", 0)} for r in rows],
                                   sorted(graded), k_values=spec.get("pass_k", []))
            for m in spec["metrics"]:
                mid, value = m["id"], report.get("metrics", {}).get(m["id"])
                if mid == "cost_per_success" and not all(_finite(r.get("cost")) for r in rows):
                    continue
                if mid in recomputed and _finite(value) and value != recomputed[mid]:
                    problems.append(f"{name}: metric {mid} disagrees with row aggregation")
    if keys_by_report[0] != keys_by_report[1]:
        problems.append("PR and baseline (item_id, seed) sets differ")
    if slices_by_report[0] != slices_by_report[1]:
        problems.append("PR and baseline item slice tags differ")
    if spec.get("seeds") is not None and set(pr["manifest"].get("seeds", [])) != set(spec["seeds"]):
        problems.append("PR seeds differ from the metric spec")
    if base_spec is not None and spec.get("eval_set_version") == base_spec.get("eval_set_version"):
        for section in ("metrics", "slices"):
            key = (lambda r: r["id"]) if section == "metrics" else (lambda r: (r["name"], r["metric"]))
            current = {key(r): r for r in spec.get(section, [])}
            for old in base_spec.get(section, []):
                rid = key(old)
                new = current.get(rid)
                if new is None:
                    problems.append(f"ratchet: removed {section} rule {rid}")
                    continue
                if old.get("blocking") and not new.get("blocking"):
                    problems.append(f"ratchet: {rid} is no longer blocking")
                before = old.get("threshold", {}) if section == "metrics" else old
                after = new.get("threshold", {}) if section == "metrics" else new
                for bound in ("min", "max", "max_increase"):
                    if bound in before and (bound not in after or not _finite(after[bound])
                            or (after[bound] < before[bound] if bound == "min" else after[bound] > before[bound])):
                        problems.append(f"ratchet: {rid} loosened {bound}")
                if before.get("no_significant_drop") and not after.get("no_significant_drop"):
                    problems.append(f"ratchet: {rid} dropped no_significant_drop")
    return problems


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(value)


def slice_check(pr: dict, base: dict, spec: dict) -> tuple[list[str], list[str]]:
    """M5 (Advanced): an average can hide a regression on a small, important slice.
    For each entry of spec["slices"] ({"name", "metric", "min"?, "no_significant_drop"?, "blocking"}):
      - value = mean of per_item(pr, metric, slice_name=name)
      - fail if below "min", or if no_significant_drop and the paired CI over the slice's items
        lies entirely below 0
    Return (blocked ids like "slice:money_movement/pass_rate", report lines)."""
    blocked, lines = [], []
    bs = spec.get("bootstrap", {})
    for rule in spec.get("slices", []):
        name, mid = rule["name"], rule["metric"]
        sid = f"slice:{name}/{mid}"
        a, b = per_item(pr, mid, name), per_item(base, mid, name)
        value = sum(a.values()) / len(a) if a else None
        failed = value is None or not b or set(a) != set(b)
        note = ""
        if value is not None and "min" in rule and value < rule["min"]:
            failed = True
        if not failed and rule.get("no_significant_drop"):
            keys = sorted(a)
            if len(keys) == 1:
                d = lo = hi = a[keys[0]] - b[keys[0]]
            else:
                d, lo, hi = stats.paired_bootstrap(
                    [a[k] for k in keys], [b[k] for k in keys],
                    n_resamples=bs.get("n_resamples", 1000), rng=bs.get("rng", 2027),
                    confidence=bs.get("confidence", 0.95))
            note = f"Δ {d:+.3f} CI [{lo:+.3f}, {hi:+.3f}]"
            failed = hi < 0
        if failed and rule.get("blocking"):
            blocked.append(sid)
        verdict = ("BLOCK" if rule.get("blocking") else "warn") if failed else "ok"
        lines.append(f"  {sid:<40} {value!s:<8} {note:<28} {verdict}")
    return blocked, lines


def check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> tuple[list[str], list[str]]:
    if pr["manifest"]["eval_set_sha"] != base["manifest"]["eval_set_sha"]:
        return ["eval_set_sha"], ["  EVAL SET differs from the baseline's: re-run the baseline on the same eval set"]
    problems = integrity_check(pr, base, spec, base_spec)
    if problems:
        return ["integrity"], [f"  INTEGRITY {p}" for p in problems]
    blocked, lines = [], []
    bs = spec.get("bootstrap", {})
    for m in spec["metrics"]:
        mid, th, val = m["id"], m.get("threshold", {}), pr["metrics"].get(m["id"])
        verdict, note = "ok", ""
        if val is None:
            verdict = "missing"
        elif "max" in th and val > th["max"]:
            verdict = "fail"
        elif "min" in th and val < th["min"]:
            verdict = "fail"
        elif "max_increase" in th and base["metrics"].get(mid):
            if val > base["metrics"][mid] * (1 + th["max_increase"]):
                verdict = "fail"
        if th.get("no_significant_drop") and val is not None:
            a, b = per_item(pr, mid), per_item(base, mid)
            keys = sorted(set(a) & set(b))
            d, lo, hi = stats.paired_bootstrap([a[k] for k in keys], [b[k] for k in keys],
                                               n_resamples=bs.get("n_resamples", 1000), rng=bs.get("rng", 2027))
            note = f"Δ {d:+.3f} CI [{lo:+.3f}, {hi:+.3f}]"
            if hi < 0:
                verdict = "fail"
        if verdict != "ok" and m.get("blocking"):
            blocked.append(mid)
        label = {"ok": "ok", "missing": "MISSING"}.get(verdict, "BLOCK" if m.get("blocking") else "warn")
        lines.append(f"  {mid:<22} {val!s:<8} {json.dumps(th):<40} {note:<28} {label}")
    if spec.get("slices"):
        sb, sl = slice_check(pr, base, spec)
        blocked += sb
        lines += sl
    return blocked, lines


def main(argv: list[str] | None = None) -> int:
    utf8_stdio()
    ap = argparse.ArgumentParser(prog="lab9.gate")
    ap.add_argument("--pr", required=True)
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--spec", default=str(SPEC))
    ap.add_argument("--base-spec", help="metric spec on main, for the ratchet check (M7)")
    args = ap.parse_args(argv)
    pr = json.loads(Path(args.pr).read_text(encoding="utf-8"))
    base = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    base_spec = load_spec(Path(args.base_spec)) if args.base_spec else None
    blocked, lines = check(pr, base, load_spec(Path(args.spec)), base_spec)
    print(f"gate: PR {pr['run_id']} vs main {base['run_id']} · eval set {pr['manifest']['eval_set_sha']}")
    print("\n".join(lines))
    if blocked:
        print(f"exit 1 — blocked: {', '.join(blocked)}")
        return 1
    print("exit 0 — merge")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
