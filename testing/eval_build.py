import json
import sys
import ast
import shutil
import tempfile
import time
from pathlib import Path
from collections import defaultdict
import csv
import multiprocessing
from multiprocessing import Manager
from concurrent.futures import ProcessPoolExecutor, as_completed
from tqdm import tqdm

# -------------------------------------------------------
# PATH SETUP
# -------------------------------------------------------

THIS_DIR = Path(__file__).parent
ROOT_DIR = THIS_DIR.parent

sys.path.insert(0, str(ROOT_DIR))

# -------------------------------------------------------
# CONFIG
# -------------------------------------------------------

SUBSETS_FILE = THIS_DIR / "eval_set/subsets.json"
INDEX_URL    = "http://127.0.0.1:8080/core-tools-v1.zip"
INDEX_ROOT   = ROOT_DIR / "toolstorepy_workspace/index_db"

OUT_DIR = THIS_DIR / "eval_set/build_eval"
OUT_DIR.mkdir(parents=True, exist_ok=True)

NUM_WORKERS = 12
BATCH_SIZE  = 32

# -------------------------------------------------------
# FLATTEN
# -------------------------------------------------------

def load_subsets(path):
    with open(path) as f:
        raw = json.load(f)
    subsets = []
    for i, entry in enumerate(raw):
        subset = entry["queries"] if isinstance(entry, dict) else entry
        git_links = [item["git_link"] for item in subset]
        subsets.append({
            "subset_index": i,
            "subset_size":  len(subset),
            "git_links":    git_links,
        })
    return subsets

# -------------------------------------------------------
# AST ANALYSIS
# -------------------------------------------------------

def analyse_ast(source: str) -> dict:
    result = {
        "valid":        False,
        "syntax_error": None,
        "tool_count":   0,
        "error_types":  defaultdict(int),
    }

    try:
        tree = ast.parse(source)
        result["valid"] = True
    except SyntaxError as e:
        result["syntax_error"] = f"{type(e).__name__}: {e.msg} (line {e.lineno})"
        result["error_types"]["SyntaxError"] += 1
        return result

    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id.startswith("_") and node.id not in dir(__builtins__):
                result["error_types"]["PotentialUndefinedName"] += 1

        if isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                result["error_types"]["RelativeImport"] += 1

        if isinstance(node, ast.ExceptHandler) and node.type is None:
            result["error_types"]["BareExcept"] += 1

        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name in dir(__builtins__):
                result["error_types"]["BuiltinRedefined"] += 1

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                is_tool = (
                    (isinstance(dec, ast.Name) and dec.id == "tool") or
                    (isinstance(dec, ast.Attribute) and dec.attr == "tool") or
                    (isinstance(dec, ast.Call) and (
                        (isinstance(dec.func, ast.Name) and dec.func.id == "tool") or
                        (isinstance(dec.func, ast.Attribute) and dec.func.attr == "tool")
                    ))
                )
                if is_tool:
                    result["tool_count"] += 1

    func_names = [
        node.name for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    seen = set()
    for name in func_names:
        if name in seen:
            result["error_types"]["DuplicateFunctionName"] += 1
        seen.add(name)

    return result

# -------------------------------------------------------
# BUILD ONE SERVER
# -------------------------------------------------------

def build_server(git_links: list, workspace: Path, cache=None) -> tuple:
    try:
        from loader.repo import RepoLoader
        from builder.mcp_builder import MCPBuilder

        tools_dir   = workspace / "tools"
        output_file = workspace / "mcp_unified_server.py"
        tools_dir.mkdir(parents=True, exist_ok=True)

        loader = RepoLoader(
            tools_dir,
            install=False,
            python_exec=None,
            cache=cache,
        )
        loader.process(git_links)

        builder = MCPBuilder(tools_dir, output_file, verbose=False)
        builder.build()

        source = output_file.read_text(encoding="utf-8")
        return source, True, None

    except Exception as e:
        return None, False, f"{type(e).__name__}: {e}"

# -------------------------------------------------------
# WORKER
# -------------------------------------------------------

def process_batch(args):
    batch, progress_list, cache_dir_str = args

    if str(ROOT_DIR) not in sys.path:
        sys.path.insert(0, str(ROOT_DIR))

    from loader.cache import RepoCache
    repo_cache = RepoCache(cache_dir=Path(cache_dir_str))

    results = []

    for entry in batch:
        idx   = entry["subset_index"]
        n     = entry["subset_size"]
        links = entry["git_links"]

        progress_list.append(idx)

        workspace = Path(tempfile.mkdtemp(prefix=f"toolstore_build_{idx}_"))

        try:
            t_start   = time.perf_counter()
            source, build_ok, build_err = build_server(links, workspace, repo_cache)
            t_elapsed = round(time.perf_counter() - t_start, 6)

            if build_ok:
                ast_result = analyse_ast(source)
                results.append({
                    "subset_index":  idx,
                    "subset_size":   n,
                    "build_success": 1,
                    "build_time_s":  t_elapsed,
                    "ast_valid":     int(ast_result["valid"]),
                    "tool_count":    ast_result["tool_count"],
                    "syntax_error":  ast_result["syntax_error"] or "",
                    "error_types":   dict(ast_result["error_types"]),
                    "build_error":   "",
                })
            else:
                results.append({
                    "subset_index":  idx,
                    "subset_size":   n,
                    "build_success": 0,
                    "build_time_s":  t_elapsed,
                    "ast_valid":     0,
                    "tool_count":    0,
                    "syntax_error":  "",
                    "error_types":   {},
                    "build_error":   build_err or "",
                })

        finally:
            shutil.rmtree(workspace, ignore_errors=True)

    return results

# -------------------------------------------------------
# MAIN
# -------------------------------------------------------

def main():
    from index.downloader import IndexDownloader
    from loader.cache import RepoCache

    print("Downloading / verifying index...")
    downloader = IndexDownloader(INDEX_ROOT)
    db_path = downloader.download(INDEX_URL, force_refresh=False)
    print(f"Index ready at: {db_path}\n")

    subsets = load_subsets(SUBSETS_FILE)
    total_subsets = len(subsets)
    print(f"Loaded {total_subsets} subsets")
    print(f"Workers: {NUM_WORKERS}  |  Batch size: {BATCH_SIZE}\n")

    # --------------------------------------------------
    # WARM CACHE — populate any missing repos before
    # workers start so no worker hits the network
    # --------------------------------------------------

    repo_cache = RepoCache()
    all_urls = list({
        link
        for entry in subsets
        for link in entry["git_links"]
    })

    missing = [u for u in all_urls if not repo_cache.is_cached(u)]
    if missing:
        print(f"Populating cache for {len(missing)} repos...")
        for url in tqdm(missing, desc="Caching repos",
                        unit="repo", dynamic_ncols=True, colour="yellow"):
            repo_cache.populate(url)
        print("Cache ready.\n")
    else:
        print(f"All {len(all_urls)} repos already cached.\n")

    # --------------------------------------------------
    # SPLIT INTO BATCHES
    # --------------------------------------------------

    batches = [
        subsets[i:i + BATCH_SIZE]
        for i in range(0, total_subsets, BATCH_SIZE)
    ]

    # --------------------------------------------------
    # PARALLEL BUILD + TEST WITH DUAL PROGRESS BARS
    # --------------------------------------------------

    all_results   = []
    manager       = Manager()
    progress_list = manager.list()

    with ProcessPoolExecutor(max_workers=NUM_WORKERS) as executor:

        futures = {
            executor.submit(
                process_batch,
                (b, progress_list, str(repo_cache.cache_dir))
            ): b
            for b in batches
        }

        bar_overall = tqdm(
            total=total_subsets,
            desc="Overall progress",
            unit="subset",
            position=0,
            dynamic_ncols=True,
            colour="green",
        )

        bar_active = tqdm(
            total=0,
            desc="Active subsets  ",
            bar_format="{desc}: {postfix}",
            position=1,
            dynamic_ncols=True,
        )

        seen_progress = 0

        for future in as_completed(futures):
            batch_results = future.result()
            all_results.extend(batch_results)
            bar_overall.update(len(batch_results))

            current_len   = len(progress_list)
            new_indices   = list(progress_list[seen_progress:current_len])
            seen_progress = current_len

            active_window = new_indices[-NUM_WORKERS:] if new_indices else []
            active_str    = "  |  ".join(
                f"[{idx:>5} / {total_subsets}]" for idx in active_window
            )

            built = sum(r["build_success"] for r in all_results)
            valid = sum(r["ast_valid"]     for r in all_results)
            done  = len(all_results)

            bar_overall.set_postfix({
                "build_acc": f"{built/done*100:.1f}%"  if done  else "N/A",
                "ast_acc":   f"{valid/built*100:.1f}%" if built else "N/A",
            })
            bar_active.set_postfix_str(active_str or "starting...")

        bar_overall.close()
        bar_active.close()

    print(f"\n\nAll {total_subsets} subsets done.\n")

    all_results.sort(key=lambda r: r["subset_index"])

    # -------------------------------------------------------
    # ACCUMULATE METRICS
    # -------------------------------------------------------

    build_success        = 0
    build_fail           = 0
    ast_valid_count      = 0
    ast_invalid_count    = 0
    error_type_totals    = defaultdict(int)
    syntax_errors        = []
    hits_by_size         = defaultdict(int)
    total_by_size        = defaultdict(int)
    ast_valid_by_size    = defaultdict(int)
    tool_counts_by_size  = defaultdict(list)
    build_times_all      = []
    build_times_by_size  = defaultdict(list)

    for r in tqdm(all_results, desc="Aggregating metrics",
                  unit="subset", dynamic_ncols=True, colour="blue"):
        n   = r["subset_size"]
        idx = r["subset_index"]

        total_by_size[n]      += 1
        build_times_all.append(r["build_time_s"])
        build_times_by_size[n].append(r["build_time_s"])

        if r["build_success"]:
            build_success       += 1
            hits_by_size[n]     += 1

            if r["ast_valid"]:
                ast_valid_count      += 1
                ast_valid_by_size[n] += 1
            else:
                ast_invalid_count    += 1
                if r["syntax_error"]:
                    syntax_errors.append((idx, r["syntax_error"]))

            for err_type, count in r["error_types"].items():
                error_type_totals[err_type] += count

            tool_counts_by_size[n].append(r["tool_count"])
        else:
            build_fail += 1

    # -------------------------------------------------------
    # HELPERS
    # -------------------------------------------------------

    def stats(values):
        if not values:
            return None, None, None, None
        s = sorted(values)
        return (
            round(sum(s) / len(s), 6),
            round(s[0], 6),
            round(s[-1], 6),
            round(s[len(s) // 2], 6),
        )

    def avg(lst):
        return round(sum(lst) / len(lst), 4) if lst else 0.0

    # -------------------------------------------------------
    # CSV 1 — Raw build + AST accuracy
    # -------------------------------------------------------

    build_accuracy = round(build_success   / total_subsets  * 100, 4)
    ast_accuracy   = round(ast_valid_count / build_success  * 100, 4) if build_success else 0.0
    e2e_accuracy   = round(ast_valid_count / total_subsets  * 100, 4)

    with open(OUT_DIR / "1_raw_build_accuracy.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["metric", "value"])
        writer.writerow(["total_subsets",           total_subsets])
        writer.writerow(["build_success",            build_success])
        writer.writerow(["build_fail",               build_fail])
        writer.writerow(["build_accuracy_pct",       build_accuracy])
        writer.writerow(["ast_valid",                ast_valid_count])
        writer.writerow(["ast_invalid",              ast_invalid_count])
        writer.writerow(["ast_accuracy_pct",         ast_accuracy])
        writer.writerow(["end_to_end_accuracy_pct",  e2e_accuracy])

    # -------------------------------------------------------
    # CSV 2 — Error type counts
    # -------------------------------------------------------

    with open(OUT_DIR / "2_error_type_counts.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["error_type", "total_count"])
        for err_type, count in sorted(error_type_totals.items(), key=lambda x: -x[1]):
            writer.writerow([err_type, count])

    # -------------------------------------------------------
    # CSV 3 — Accuracy by subset size
    # -------------------------------------------------------

    with open(OUT_DIR / "3_accuracy_by_size.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "subset_size", "build_success", "total_subsets", "build_accuracy_pct",
            "ast_valid", "ast_accuracy_pct", "avg_tool_count"
        ])
        for n in range(1, 16):
            bs    = hits_by_size[n]
            total = total_by_size[n]
            av    = ast_valid_by_size[n]
            tc    = avg(tool_counts_by_size[n])
            b_acc = round(bs / total * 100, 4) if total else 0.0
            a_acc = round(av / bs   * 100, 4) if bs    else 0.0
            writer.writerow([n, bs, total, b_acc, av, a_acc, tc])

    # -------------------------------------------------------
    # CSV 4 — Syntax errors log
    # -------------------------------------------------------

    with open(OUT_DIR / "4_syntax_errors.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["subset_index", "syntax_error"])
        for idx, msg in syntax_errors:
            writer.writerow([idx, msg])

    # -------------------------------------------------------
    # CSV 5 — Detailed results
    # -------------------------------------------------------

    with open(OUT_DIR / "5_detailed_results.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "subset_index", "subset_size", "build_success", "build_time_s",
            "ast_valid", "tool_count", "syntax_error", "error_types", "build_error"
        ])
        for r in all_results:
            writer.writerow([
                r["subset_index"], r["subset_size"], r["build_success"],
                r["build_time_s"], r["ast_valid"],   r["tool_count"],
                r["syntax_error"], json.dumps(r["error_types"]), r["build_error"]
            ])

    # -------------------------------------------------------
    # CSV 6 — Raw build timing
    # -------------------------------------------------------

    avg_t, min_t, max_t, med_t = stats(build_times_all)

    with open(OUT_DIR / "6_raw_build_timing.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["metric", "value"])
        writer.writerow(["total_builds",        total_subsets])
        writer.writerow(["avg_build_time_s",    avg_t])
        writer.writerow(["median_build_time_s", med_t])
        writer.writerow(["min_build_time_s",    min_t])
        writer.writerow(["max_build_time_s",    max_t])

    # -------------------------------------------------------
    # CSV 7 — Build time by subset size
    # -------------------------------------------------------

    with open(OUT_DIR / "7_build_time_by_size.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "subset_size", "total_builds",
            "avg_build_time_s", "median_build_time_s",
            "min_build_time_s", "max_build_time_s"
        ])
        for n in range(1, 16):
            times = build_times_by_size[n]
            a, mn, mx, md = stats(times)
            writer.writerow([n, len(times), a, md, mn, mx])

    # -------------------------------------------------------
    # TXT — Summary
    # -------------------------------------------------------

    avg_bt, min_bt, max_bt, med_bt = stats(build_times_all)

    summary = f"""
=====================================
 MCP BUILD + AST EVALUATION REPORT
=====================================
Workers                  : {NUM_WORKERS}
Batch size               : {BATCH_SIZE}
Cache dir                : {repo_cache.cache_dir}

--- BUILD ACCURACY ---
Total subsets            : {total_subsets}
Build success            : {build_success}
Build fail               : {build_fail}
Build accuracy           : {build_accuracy}%

--- AST ACCURACY (of successful builds) ---
AST valid                : {ast_valid_count}
AST invalid              : {ast_invalid_count}
AST accuracy             : {ast_accuracy}%

--- END-TO-END ACCURACY ---
End-to-end accuracy      : {e2e_accuracy}%

--- BUILD TIMING ---
Avg build time           : {avg_bt}s
Median build time        : {med_bt}s
Min build time           : {min_bt}s
Max build time           : {max_bt}s

--- ERROR TYPE COUNTS ---
""".lstrip()

    for err_type, count in sorted(error_type_totals.items(), key=lambda x: -x[1]):
        summary += f"  {err_type:<30} : {count}\n"

    summary += f"""
--- BREAKDOWN BY SUBSET SIZE ---
{"size":>5}  {"b_ok":>6}  {"tot":>6}  {"b_acc%":>7}  {"ast_ok":>7}  {"a_acc%":>7}  {"avg_tools":>10}  {"avg_time_s":>11}
"""
    for n in range(1, 16):
        bs    = hits_by_size[n]
        total = total_by_size[n]
        av    = ast_valid_by_size[n]
        tc    = avg(tool_counts_by_size[n])
        a, mn, mx, md = stats(build_times_by_size[n])
        b_acc = round(bs / total * 100, 2) if total else 0.0
        a_acc = round(av / bs   * 100, 2) if bs    else 0.0
        summary += f"{n:>5}  {bs:>6}  {total:>6}  {b_acc:>7}  {av:>7}  {a_acc:>7}  {tc:>10}  {a:>11}\n"

    with open(OUT_DIR / "summary.txt", "w") as f:
        f.write(summary)

    print("\n" + summary)
    print(f"All results saved to: {OUT_DIR}/")


if __name__ == "__main__":
    main()