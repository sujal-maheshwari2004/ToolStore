import json
import sys
import time
import csv
import random
import string
from pathlib import Path
from collections import defaultdict
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

SUBSETS_FILE  = THIS_DIR / "eval_set/subsets.json"
INDEX_URL     = "http://127.0.0.1:8080/core-tools-v1.zip"
INDEX_ROOT    = ROOT_DIR / "toolstorepy_workspace/index_db"
ENCODER       = "all-MiniLM-L6-v2"
CROSS_ENCODER = "cross-encoder/ms-marco-MiniLM-L-6-v2"
ENCODE_BATCH  = 512
RERANK_BATCH  = 256

OUT_DIR = THIS_DIR / "eval_set/retrieval_eval"
OUT_DIR.mkdir(parents=True, exist_ok=True)

RANDOM_SEED = 42

# -------------------------------------------------------
# LOGGING HELPERS
# -------------------------------------------------------

def section(title: str):
    width = 54
    tqdm.write("\n" + "=" * width)
    tqdm.write(f"  {title}")
    tqdm.write("=" * width)

def step(icon: str, msg: str):
    tqdm.write(f"  {icon}  {msg}")

def substep(msg: str):
    tqdm.write(f"       ↳  {msg}")

def done(msg: str):
    tqdm.write(f"  ✔  {msg}")

def separator():
    tqdm.write("  " + "-" * 50)

# -------------------------------------------------------
# SYNONYM MAP
# -------------------------------------------------------

SYNONYMS = {
    "file":        ["document", "record", "archive"],
    "text":        ["content", "string", "words"],
    "image":       ["picture", "photo", "graphic"],
    "hash":        ["checksum", "digest", "fingerprint"],
    "token":       ["key", "secret", "credential"],
    "container":   ["instance", "pod", "box"],
    "log":         ["record", "output", "trace"],
    "repository":  ["repo", "codebase", "project"],
    "commit":      ["revision", "change", "snapshot"],
    "host":        ["server", "machine", "node"],
    "domain":      ["hostname", "address", "url"],
    "currency":    ["money", "funds", "exchange"],
    "amount":      ["value", "sum", "quantity"],
    "city":        ["location", "place", "town"],
    "temperature": ["temp", "heat", "degrees"],
    "note":        ["entry", "memo", "record"],
    "keyword":     ["term", "tag", "label"],
    "expression":  ["formula", "equation", "calculation"],
    "process":     ["task", "job", "program"],
    "disk":        ["storage", "drive", "volume"],
    "data":        ["info", "records", "content"],
    "rows":        ["entries", "lines", "records"],
    "metadata":    ["attributes", "properties", "info"],
    "page":        ["sheet", "leaf", "section"],
    "uuid":        ["identifier", "id", "guid"],
}

# -------------------------------------------------------
# PERTURBATION FUNCTIONS
# -------------------------------------------------------

def perturb_remove_token(query: str, rng: random.Random) -> str:
    tokens = query.split()
    if len(tokens) <= 1:
        return query
    tokens.pop(rng.randint(0, len(tokens) - 1))
    return " ".join(tokens)


def perturb_add_token(query: str, rng: random.Random) -> str:
    fillers = [
        "quickly", "easily", "safely", "automatically", "efficiently",
        "simple", "basic", "fast", "local", "remote", "custom", "new",
        "given", "specific", "multiple", "single", "current", "external",
    ]
    tokens = query.split()
    tokens.insert(rng.randint(0, len(tokens)), rng.choice(fillers))
    return " ".join(tokens)


def perturb_add_char(query: str, rng: random.Random) -> str:
    tokens = query.split()
    if not tokens:
        return query
    idx   = rng.randint(0, len(tokens) - 1)
    token = tokens[idx]
    pos   = rng.randint(0, len(token))
    tokens[idx] = token[:pos] + rng.choice(string.ascii_lowercase) + token[pos:]
    return " ".join(tokens)


def perturb_synonym(query: str, rng: random.Random) -> str:
    tokens = query.split()
    candidates = [
        i for i, t in enumerate(tokens)
        if t.lower().rstrip("s") in SYNONYMS or t.lower() in SYNONYMS
    ]
    if not candidates:
        return query
    idx   = rng.choice(candidates)
    token = tokens[idx].lower()
    key   = token if token in SYNONYMS else token.rstrip("s")
    tokens[idx] = rng.choice(SYNONYMS[key])
    return " ".join(tokens)


VARIANTS = [
    ("original",     None,                 "Original queries"),
    ("remove_token", perturb_remove_token, "One random token removed per query"),
    ("add_token",    perturb_add_token,    "One random filler token inserted per query"),
    ("add_char",     perturb_add_char,     "One random character inserted into a token"),
    ("synonym",      perturb_synonym,      "Noun/object token replaced with synonym"),
]

# -------------------------------------------------------
# DETECT DEVICE
# -------------------------------------------------------

def get_device():
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda", torch.cuda.get_device_name(0)
        elif torch.backends.mps.is_available():
            return "mps", "Apple MPS"
    except ImportError:
        pass
    return "cpu", "CPU"

# -------------------------------------------------------
# FLATTEN
# -------------------------------------------------------

def flatten_queries(queries_sets, perturb_fn=None, rng=None):
    flat = []
    subset_sizes = {}
    for i, entry in enumerate(queries_sets):
        subset = entry["queries"] if isinstance(entry, dict) else entry
        n = len(subset)
        subset_sizes[i] = n
        for item in subset:
            q = item["query"]
            if perturb_fn is not None:
                q = perturb_fn(q, rng)
            flat.append({
                "subset_index":   i,
                "subset_size":    n,
                "git_link":       item["git_link"],
                "query":          q,
                "original_query": item["query"],
            })
    return flat, subset_sizes

# -------------------------------------------------------
# PARSE CHUNK
# -------------------------------------------------------

def parse_chunk(chunk_text):
    for line in chunk_text.split("\n"):
        if line.startswith("Git Link:"):
            return line.replace("Git Link:", "").strip()
    return None

# -------------------------------------------------------
# RUN ONE VARIANT
# -------------------------------------------------------

def run_variant(variant_name, perturb_fn, queries_sets, encoder,
                reranker, collection, device, rng, v_idx, v_total):

    rng_copy = random.Random(RANDOM_SEED)

    # -- flatten --
    step("📋", f"[{v_idx}/{v_total}] Flattening queries  ({variant_name})")
    t0 = time.perf_counter()
    flat_queries, subset_sizes = flatten_queries(
        queries_sets, perturb_fn=perturb_fn, rng=rng_copy,
    )
    queries_text = [q["query"] for q in flat_queries]
    substep(f"{len(flat_queries):,} queries built  ({time.perf_counter()-t0:.2f}s)")

    # -- encode --
    step("🔢", f"[{v_idx}/{v_total}] Encoding  ({variant_name})")
    t0 = time.perf_counter()
    all_embeddings = encoder.encode(
        queries_text,
        batch_size=ENCODE_BATCH,
        show_progress_bar=True,
        convert_to_numpy=True,
        device=device,
    )
    substep(f"Encoded {len(all_embeddings):,} queries  ({time.perf_counter()-t0:.2f}s)")

    # -- retrieve --
    step("🔍", f"[{v_idx}/{v_total}] Retrieving from ChromaDB  ({variant_name})")
    TOP_K = 10
    t0 = time.perf_counter()
    retrieved_docs = []
    for emb in tqdm(all_embeddings, desc=f"  ChromaDB [{variant_name}]",
                    unit="q", dynamic_ncols=True, colour="yellow", leave=False):
        results = collection.query(
            query_embeddings=[emb.tolist()],
            n_results=TOP_K,
            include=["documents"],
        )
        docs = results["documents"][0] if results["documents"] else []
        retrieved_docs.append(docs)
    substep(f"Retrieved top-{TOP_K} docs for {len(retrieved_docs):,} queries  ({time.perf_counter()-t0:.2f}s)")

    # -- build pairs --
    step("🔗", f"[{v_idx}/{v_total}] Building rerank pairs  ({variant_name})")
    t0 = time.perf_counter()
    pair_map  = []
    all_pairs = []
    for qi, (query, docs) in enumerate(zip(queries_text, retrieved_docs)):
        for di, doc in enumerate(docs):
            pair_map.append((qi, di))
            all_pairs.append([query, doc])
    substep(f"{len(all_pairs):,} pairs built  ({time.perf_counter()-t0:.2f}s)")

    # -- rerank --
    step("🏆", f"[{v_idx}/{v_total}] Reranking  ({variant_name})")
    t0 = time.perf_counter()
    all_scores = reranker.predict(
        all_pairs,
        batch_size=RERANK_BATCH,
        show_progress_bar=True,
    )
    substep(f"Scored {len(all_scores):,} pairs  ({time.perf_counter()-t0:.2f}s)")

    # -- group scores --
    query_pair_scores = defaultdict(list)
    for (qi, di), score in zip(pair_map, all_scores):
        query_pair_scores[qi].append((float(score), retrieved_docs[qi][di]))

    # -- parse results --
    step("📊", f"[{v_idx}/{v_total}] Parsing results  ({variant_name})")
    t0 = time.perf_counter()
    results = []
    for qi, item in enumerate(flat_queries):
        pairs = query_pair_scores[qi]
        if pairs:
            best_score, best_doc = max(pairs, key=lambda x: x[0])
            retrieved_git = parse_chunk(best_doc)
            score         = best_score
        else:
            retrieved_git = None
            score         = None

        hit = int(retrieved_git == item["git_link"])
        results.append({
            "variant":         variant_name,
            "subset_index":    item["subset_index"],
            "subset_size":     item["subset_size"],
            "original_query":  item["original_query"],
            "perturbed_query": item["query"],
            "expected_git":    item["git_link"],
            "retrieved_git":   retrieved_git,
            "rerank_score":    round(score, 6) if score is not None else None,
            "hit":             hit,
        })

    correct = sum(r["hit"] for r in results)
    total   = len(results)
    substep(f"Parsed {total:,} results  ({time.perf_counter()-t0:.2f}s)")
    done(f"[{v_idx}/{v_total}] {variant_name} complete  →  "
         f"accuracy: {correct}/{total} = {correct/total*100:.2f}%")

    return results, subset_sizes

# -------------------------------------------------------
# AGGREGATE
# -------------------------------------------------------

def aggregate(results, subset_sizes):
    all_hit_flags                = []
    hits_by_size                 = defaultdict(int)
    total_by_size                = defaultdict(int)
    subset_fully_correct_by_size = defaultdict(int)
    subset_total_by_size         = defaultdict(int)
    scores_hits                  = []
    scores_misses                = []
    subset_hit_counts            = defaultdict(int)

    for r in results:
        n   = r["subset_size"]
        hit = r["hit"]
        s   = r["rerank_score"]
        idx = r["subset_index"]

        all_hit_flags.append(hit)
        hits_by_size[n]  += hit
        total_by_size[n] += 1
        subset_hit_counts[idx] += hit

        if s is not None:
            (scores_hits if hit else scores_misses).append(s)

    for idx, size in subset_sizes.items():
        subset_total_by_size[size] += 1
        if subset_hit_counts[idx] == size:
            subset_fully_correct_by_size[size] += 1

    return {
        "all_hit_flags":                all_hit_flags,
        "hits_by_size":                 hits_by_size,
        "total_by_size":                total_by_size,
        "subset_fully_correct_by_size": subset_fully_correct_by_size,
        "subset_total_by_size":         subset_total_by_size,
        "scores_hits":                  scores_hits,
        "scores_misses":                scores_misses,
    }

# -------------------------------------------------------
# HELPERS
# -------------------------------------------------------

def stats(values):
    if not values:
        return None, None, None, None
    avg = sum(values) / len(values)
    mn  = min(values)
    mx  = max(values)
    med = sorted(values)[len(values) // 2]
    return round(avg, 6), round(mn, 6), round(mx, 6), round(med, 6)

# -------------------------------------------------------
# MAIN
# -------------------------------------------------------

def main():

    wall_start = time.perf_counter()

    # -------------------------------------------------------
    # STAGE 0 — INIT
    # -------------------------------------------------------

    section("STAGE 0 — INITIALISATION")

    device, device_name = get_device()
    step("💻", f"Device       : {device_name}  ({device.upper()})")
    step("📦", f"Encoder      : {ENCODER}")
    step("📦", f"Cross-encoder: {CROSS_ENCODER}")
    step("🔢", f"Encode batch : {ENCODE_BATCH}  |  Rerank batch: {RERANK_BATCH}")
    step("🎲", f"Random seed  : {RANDOM_SEED}")
    step("📁", f"Output dir   : {OUT_DIR}")

    # -------------------------------------------------------
    # STAGE 1 — INDEX
    # -------------------------------------------------------

    section("STAGE 1 — INDEX")

    from index.downloader import IndexDownloader
    from sentence_transformers import SentenceTransformer, CrossEncoder
    from chromadb import PersistentClient

    step("⬇️ ", "Downloading / verifying index...")
    t0 = time.perf_counter()
    downloader = IndexDownloader(INDEX_ROOT)
    db_path    = downloader.download(INDEX_URL, force_refresh=False)
    done(f"Index ready  ({time.perf_counter()-t0:.2f}s)  →  {db_path}")

    # -------------------------------------------------------
    # STAGE 2 — MODELS
    # -------------------------------------------------------

    section("STAGE 2 — MODEL LOADING")

    step("🧠", f"Loading encoder: {ENCODER}")
    t0 = time.perf_counter()
    encoder = SentenceTransformer(ENCODER, device=device)
    done(f"Encoder loaded  ({time.perf_counter()-t0:.2f}s)")

    step("🧠", f"Loading cross-encoder: {CROSS_ENCODER}")
    t0 = time.perf_counter()
    reranker = CrossEncoder(CROSS_ENCODER, device=device)
    done(f"Cross-encoder loaded  ({time.perf_counter()-t0:.2f}s)")

    step("🗄️ ", "Connecting to ChromaDB...")
    t0 = time.perf_counter()
    chroma     = PersistentClient(path=str(db_path))
    collection = chroma.get_or_create_collection("tools")
    done(f"ChromaDB ready  ({time.perf_counter()-t0:.2f}s)")

    # -------------------------------------------------------
    # STAGE 3 — DATA
    # -------------------------------------------------------

    section("STAGE 3 — DATA LOADING")

    step("📂", f"Loading subsets from {SUBSETS_FILE.name}...")
    t0 = time.perf_counter()
    with open(SUBSETS_FILE) as f:
        queries_sets = json.load(f)
    done(f"Loaded {len(queries_sets):,} subsets  ({time.perf_counter()-t0:.2f}s)")
    step("🔀", f"Variants to run: {len(VARIANTS)}")
    for vname, _, desc in VARIANTS:
        substep(f"{vname:<15} — {desc}")

    rng = random.Random(RANDOM_SEED)

    # -------------------------------------------------------
    # STAGE 4 — VARIANTS
    # -------------------------------------------------------

    section("STAGE 4 — VARIANT EVALUATION")

    all_variant_results = {}
    all_variant_metrics = {}
    all_variant_sizes   = {}
    v_total = len(VARIANTS)

    for v_idx, (variant_name, perturb_fn, description) in enumerate(VARIANTS, 1):
        separator()
        t_var = time.perf_counter()

        results, subset_sizes = run_variant(
            variant_name, perturb_fn, queries_sets,
            encoder, reranker, collection, device,
            rng, v_idx, v_total,
        )

        all_variant_results[variant_name] = results
        all_variant_metrics[variant_name] = aggregate(results, subset_sizes)
        all_variant_sizes[variant_name]   = subset_sizes

        substep(f"Variant wall time: {time.perf_counter()-t_var:.2f}s")

    # -------------------------------------------------------
    # STAGE 5 — WRITE OUTPUTS
    # -------------------------------------------------------

    section("STAGE 5 — WRITING OUTPUTS")

    with tqdm(total=7, desc="  Writing files", unit="file",
              dynamic_ncols=True, colour="magenta") as pbar:

        # CSV 1
        step("💾", "1_variant_raw_accuracy.csv")
        with open(OUT_DIR / "1_variant_raw_accuracy.csv", "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "variant", "total_queries", "correct", "wrong",
                "raw_accuracy_pct", "total_subsets", "fully_correct_subsets",
                "subset_accuracy_pct", "avg_score_hits", "avg_score_misses",
            ])
            for vname, _, _ in VARIANTS:
                m     = all_variant_metrics[vname]
                flags = m["all_hit_flags"]
                total = len(flags)
                corr  = sum(flags)
                acc   = round(corr / total * 100, 4) if total else 0.0
                ts    = sum(m["subset_total_by_size"].values())
                tsc   = sum(m["subset_fully_correct_by_size"].values())
                sa    = round(tsc / ts * 100, 4) if ts else 0.0
                ash, *_ = stats(m["scores_hits"])
                asm, *_ = stats(m["scores_misses"])
                writer.writerow([vname, total, corr, total - corr, acc,
                                 ts, tsc, sa, ash, asm])
        pbar.update(1)

        # CSV 2
        step("💾", "2_accuracy_by_size_all_variants.csv")
        with open(OUT_DIR / "2_accuracy_by_size_all_variants.csv", "w", newline="") as f:
            writer = csv.writer(f)
            header_row = ["subset_size"]
            for vname, _, _ in VARIANTS:
                header_row += [
                    f"{vname}_correct_q", f"{vname}_total_q", f"{vname}_q_acc_pct",
                    f"{vname}_correct_s", f"{vname}_total_s", f"{vname}_s_acc_pct",
                ]
            writer.writerow(header_row)
            for n in range(1, 16):
                row = [n]
                for vname, _, _ in VARIANTS:
                    m  = all_variant_metrics[vname]
                    qh = m["hits_by_size"][n]
                    qt = m["total_by_size"][n]
                    qa = round(qh / qt * 100, 4) if qt else 0.0
                    sh = m["subset_fully_correct_by_size"][n]
                    st = m["subset_total_by_size"][n]
                    sa = round(sh / st * 100, 4) if st else 0.0
                    row += [qh, qt, qa, sh, st, sa]
                writer.writerow(row)
        pbar.update(1)

        # CSV 3
        step("💾", "3_robustness_delta_vs_original.csv")
        with open(OUT_DIR / "3_robustness_delta_vs_original.csv", "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "variant", "description",
                "original_q_acc_pct", "variant_q_acc_pct", "q_acc_delta",
                "original_s_acc_pct", "variant_s_acc_pct", "s_acc_delta",
            ])
            orig_m   = all_variant_metrics["original"]
            orig_qf  = orig_m["all_hit_flags"]
            orig_qa  = round(sum(orig_qf) / len(orig_qf) * 100, 4)
            orig_ts  = sum(orig_m["subset_total_by_size"].values())
            orig_tsc = sum(orig_m["subset_fully_correct_by_size"].values())
            orig_sa  = round(orig_tsc / orig_ts * 100, 4) if orig_ts else 0.0
            for vname, _, desc in VARIANTS:
                if vname == "original":
                    continue
                m     = all_variant_metrics[vname]
                flags = m["all_hit_flags"]
                qa    = round(sum(flags) / len(flags) * 100, 4) if flags else 0.0
                ts    = sum(m["subset_total_by_size"].values())
                tsc   = sum(m["subset_fully_correct_by_size"].values())
                sa    = round(tsc / ts * 100, 4) if ts else 0.0
                writer.writerow([vname, desc, orig_qa, qa, round(qa - orig_qa, 4),
                                 orig_sa, sa, round(sa - orig_sa, 4)])
        pbar.update(1)

        # CSV 4
        step("💾", "4_rerank_scores_per_variant.csv")
        with open(OUT_DIR / "4_rerank_scores_per_variant.csv", "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["variant", "category", "count",
                             "avg_score", "median_score", "min_score", "max_score"])
            for vname, _, _ in VARIANTS:
                m = all_variant_metrics[vname]
                for label, scores in [("hits", m["scores_hits"]), ("misses", m["scores_misses"])]:
                    a, mn, mx, med = stats(scores)
                    writer.writerow([vname, label, len(scores), a, med, mn, mx])
        pbar.update(1)

        # CSV 5
        step("💾", "5_detailed_results_all_variants.csv")
        with open(OUT_DIR / "5_detailed_results_all_variants.csv", "w", newline="") as f:
            fieldnames = [
                "variant", "subset_index", "subset_size",
                "original_query", "perturbed_query",
                "expected_git", "retrieved_git", "rerank_score", "hit",
            ]
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for vname, _, _ in VARIANTS:
                writer.writerows(all_variant_results[vname])
        pbar.update(1)

        # CSV 6
        step("💾", "6_flip_analysis.csv")
        orig_hits = {
            (r["subset_index"], r["original_query"]): r["hit"]
            for r in all_variant_results["original"]
        }
        flip_count = 0
        with open(OUT_DIR / "6_flip_analysis.csv", "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "subset_index", "subset_size", "original_query",
                "variant", "perturbed_query", "original_hit", "variant_hit", "flipped"
            ])
            for vname, _, _ in VARIANTS:
                if vname == "original":
                    continue
                for r in all_variant_results[vname]:
                    key  = (r["subset_index"], r["original_query"])
                    oh   = orig_hits.get(key, -1)
                    vh   = r["hit"]
                    if oh != vh:
                        flip_count += 1
                        writer.writerow([
                            r["subset_index"], r["subset_size"],
                            r["original_query"], vname,
                            r["perturbed_query"], oh, vh, 1
                        ])
        substep(f"{flip_count:,} flips logged")
        pbar.update(1)

        # TXT
        step("💾", "summary.txt")
        orig_qa_val = round(
            sum(all_variant_metrics["original"]["all_hit_flags"]) /
            len(all_variant_metrics["original"]["all_hit_flags"]) * 100, 4
        )
        orig_ts  = sum(all_variant_metrics["original"]["subset_total_by_size"].values())
        orig_tsc = sum(all_variant_metrics["original"]["subset_fully_correct_by_size"].values())
        orig_sa_val = round(orig_tsc / orig_ts * 100, 4) if orig_ts else 0.0

        summary = f"""
=============================================
 RETRIEVAL + RERANK EVALUATION REPORT
 (Original + 4 Perturbation Variants)
=============================================
Device           : {device_name}  ({device.upper()})
Encode batch     : {ENCODE_BATCH}
Rerank batch     : {RERANK_BATCH}
Random seed      : {RANDOM_SEED}
Total wall time  : {time.perf_counter()-wall_start:.2f}s

""".lstrip()

        for vname, _, desc in VARIANTS:
            m     = all_variant_metrics[vname]
            flags = m["all_hit_flags"]
            total = len(flags)
            corr  = sum(flags)
            acc   = round(corr / total * 100, 4) if total else 0.0
            ts    = sum(m["subset_total_by_size"].values())
            tsc   = sum(m["subset_fully_correct_by_size"].values())
            sa    = round(tsc / ts * 100, 4) if ts else 0.0
            ash, *_ = stats(m["scores_hits"])
            asm, *_ = stats(m["scores_misses"])
            summary += f"""--- {vname.upper()} ---
Description      : {desc}
Query accuracy   : {corr}/{total} = {acc}%
Subset accuracy  : {tsc}/{ts} = {sa}%
Avg score (hits) : {ash}
Avg score (miss) : {asm}

"""

        summary += "--- ROBUSTNESS DELTAS (vs original) ---\n"
        summary += f"{'variant':<15}  {'q_acc_delta':>12}  {'s_acc_delta':>12}\n"
        for vname, _, _ in VARIANTS:
            if vname == "original":
                continue
            m     = all_variant_metrics[vname]
            flags = m["all_hit_flags"]
            qa    = round(sum(flags) / len(flags) * 100, 4) if flags else 0.0
            ts    = sum(m["subset_total_by_size"].values())
            tsc   = sum(m["subset_fully_correct_by_size"].values())
            sa    = round(tsc / ts * 100, 4) if ts else 0.0
            summary += (
                f"{vname:<15}  "
                f"{round(qa - orig_qa_val, 4):>+12}  "
                f"{round(sa - orig_sa_val, 4):>+12}\n"
            )

        summary += f"""
--- BREAKDOWN BY SIZE (query accuracy %) ---
{"size":>5}  """ + "  ".join(f"{v[0]:>12}" for v in VARIANTS) + "\n"
        for n in range(1, 16):
            row = f"{n:>5}  "
            for vname, _, _ in VARIANTS:
                m  = all_variant_metrics[vname]
                qh = m["hits_by_size"][n]
                qt = m["total_by_size"][n]
                qa = round(qh / qt * 100, 2) if qt else 0.0
                row += f"{qa:>12}  "
            summary += row.rstrip() + "\n"

        with open(OUT_DIR / "summary.txt", "w") as f:
            f.write(summary)
        pbar.update(1)

    # -------------------------------------------------------
    # STAGE 6 — FINAL REPORT
    # -------------------------------------------------------

    section("STAGE 6 — FINAL REPORT")

    for vname, _, desc in VARIANTS:
        m     = all_variant_metrics[vname]
        flags = m["all_hit_flags"]
        corr  = sum(flags)
        total = len(flags)
        acc   = round(corr / total * 100, 4) if total else 0.0
        ts    = sum(m["subset_total_by_size"].values())
        tsc   = sum(m["subset_fully_correct_by_size"].values())
        sa    = round(tsc / ts * 100, 4) if ts else 0.0
        step("📈", f"{vname:<15}  q_acc={acc:>7}%  s_acc={sa:>7}%")

    separator()
    step("🔀", "Robustness deltas vs original:")
    for vname, _, _ in VARIANTS:
        if vname == "original":
            continue
        m     = all_variant_metrics[vname]
        flags = m["all_hit_flags"]
        qa    = round(sum(flags) / len(flags) * 100, 4) if flags else 0.0
        ts    = sum(m["subset_total_by_size"].values())
        tsc   = sum(m["subset_fully_correct_by_size"].values())
        sa    = round(tsc / ts * 100, 4) if ts else 0.0
        substep(
            f"{vname:<15}  "
            f"Δq={round(qa - orig_qa_val, 4):>+7}%  "
            f"Δs={round(sa - orig_sa_val, 4):>+7}%"
        )

    separator()
    done(f"Total wall time : {time.perf_counter()-wall_start:.2f}s")
    done(f"Results saved   : {OUT_DIR}/")
    tqdm.write("")


if __name__ == "__main__":
    main()