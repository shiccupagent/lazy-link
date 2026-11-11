#!/usr/bin/env python3
"""model_watch.py [--out DIR] [--all] - which new open models on Hugging Face are worth a side-by-side test against ours.

Reads the most-trending models for each category in tools/model_watch.json (public Hugging Face listing, no account,
no token), leaves out what we already use or tested (imagegen/models.json, voice/*.json, "known"), copies of other
models (quantized, adapters, merges, someone else's fine-tune), ones older than max_age_days or with fewer than
min_likes, and licences that forbid selling (non-commercial, research-only). Writes DIR/latest.md (newest first in
each category, with release date, likes, licence and a size guess), DIR/<date>.md and DIR/latest.json (every current
candidate, for pages); DIR/seen.json remembers what an
earlier report listed, so --all is needed to list those again. Exit 0 always unless Hugging Face cannot be reached.

    python3 tools/model_watch.py --out ~/model-watch"""
import argparse, datetime as dt, json, re, sys, time, urllib.parse, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
API = "https://huggingface.co/api/models"
UA = "lazy-link-model-watch/1.0"
FIELDS = ["createdAt", "likes", "trendingScore", "tags", "safetensors", "library_name", "cardData"]
COPY = re.compile(r"gguf|\bmlx\b|-mlx|int4|int8|nf4|fp8|awq|gptq|-ov$|quantized|lora|-4bit|-8bit|lowvram|workflow|comfyui|"
                  r"hybrid|pruned|controlnet|character|sheet|anything2|-fun-|merge", re.I)
OK_LICENCE = re.compile(r"^(mit|apache-2\.0|bsd.*|cc0.*|cc-by-4\.0|cc-by-sa-4\.0|openrail.*|creativeml-openrail-m|unlicense|llama.*|gemma)$", re.I)
NO_LICENCE = re.compile(r"non-?commercial|\bnc\b|-nc|research|academic|cc-by-nc", re.I)


def get(params):
    url = API + "?" + urllib.parse.urlencode(params, doseq=True)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=30) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001 - retried, then reported
            err = e
            time.sleep(3 * (attempt + 1))
    raise SystemExit(f"model_watch: Hugging Face could not be reached: {err}")


CFG = {}


def known_repos(cfg):
    """Every Hugging Face repo id our own settings already download from, plus the config's "known" list."""
    text = (ROOT / "imagegen" / "models.json").read_text(encoding="utf-8")
    for f in (ROOT / "voice").glob("*.json"):
        text += f.read_text(encoding="utf-8")
    ids = set(re.findall(r"huggingface\.co/([\w.-]+/[\w.-]+)", text))
    ids |= set(re.findall(r'"model_repo":\s*"([\w.-]+/[\w.-]+)"', text))
    ids |= set(cfg.get("known", []))
    return {i.lower() for i in ids}


def is_known(mid, known):
    """ours, or a variant of ours from the same repo family (flux-3-image-klein-base when we use flux-3-image-klein)"""
    m = mid.lower()
    return m in known or any(m.startswith(k + "-") or m.startswith(k + "_") for k in known)


def licence(m):
    card = m.get("cardData") or {}
    lic = card.get("license") or next((t.split(":", 1)[1] for t in m.get("tags", []) if t.startswith("license:")), "")
    if isinstance(lic, list):
        lic = lic[0] if lic else ""
    name = card.get("license_name") or ""
    lic = str(lic)
    if str(name).lower() in CFG.get("blocked_licences", {}) or lic.lower() in CFG.get("blocked_licences", {}):
        return "no", str(name or lic)
    if NO_LICENCE.search(lic) or NO_LICENCE.search(str(name)):
        return "no", f"{lic} {name}".strip()
    if OK_LICENCE.match(lic):
        return "ok", lic
    return "check", f"{lic or 'none given'}{' (' + str(name) + ')' if name else ''}"


def is_copy(m):
    """A quantized copy, adapter or merge of another model, or someone else's fine-tune: not a new model."""
    if COPY.search(m["id"]) or (m.get("library_name") or "") in ("gguf", "mlx", "peft"):
        return True
    author = m["id"].split("/")[0].lower()
    name = m["id"].split("/")[1].lower()
    for word, makers in CFG.get("makers", {}).items():   # "minimax" in the name of a repo MiniMaxAI did not make
        if word in name and author not in makers:
            return True
    for t in m.get("tags", []):
        if re.match(r"base_model:(quantized|adapter|merge):", t):
            return True
        if t.startswith("base_model:finetune:") and t.split(":", 2)[2].split("/")[0].lower() != author:
            return True
    return False


def size_note(m, gpu_gb):
    n = (m.get("safetensors") or {}).get("total")
    if not n:
        return "size not listed"
    gb = n * 2 / 1e9   # bf16
    fit = "fits" if gb <= gpu_gb * 0.8 else "tight" if gb <= gpu_gb * 1.6 else "too big"
    return f"{n / 1e9:.1f}B params (~{gb:.0f} GB in bf16, {fit} on {gpu_gb} GB)"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path.home() / "model-watch"))
    ap.add_argument("--config", default=str(HERE / "model_watch.json"))
    ap.add_argument("--all", action="store_true", help="also list models an earlier report already listed")
    a = ap.parse_args()
    cfg = json.loads(Path(a.config).read_text(encoding="utf-8"))
    CFG.update(cfg)
    out = Path(a.out).expanduser(); out.mkdir(parents=True, exist_ok=True)
    seen_f = out / "seen.json"
    seen = json.loads(seen_f.read_text()) if seen_f.exists() else {}
    known = known_repos(cfg)
    now = dt.datetime.now(dt.timezone.utc)
    sections, new_seen, total, listing = [], {}, 0, []
    for cat, c in cfg["categories"].items():
        rows, ids = [], set()
        for tag in c["tags"]:
            for m in get({"pipeline_tag": tag, "sort": "trendingScore", "direction": "-1", "limit": cfg["per_tag"], "expand[]": FIELDS}):
                mid = m["id"]
                if mid in ids or is_known(mid, known) or mid.split("/")[0] == "Comfy-Org" or is_copy(m):
                    continue
                if c.get("need") and not any(w in (mid + " " + " ".join(m.get("tags", []))).lower() for w in c["need"]):
                    continue
                made = dt.datetime.fromisoformat(m.get("createdAt", "1970-01-01T00:00:00Z").replace("Z", "+00:00"))
                if (now - made).days > cfg["max_age_days"] or (m.get("likes") or 0) < cfg["min_likes"]:
                    continue
                ok, lic = licence(m)
                if ok == "no":
                    continue
                ids.add(mid)
                listing.append({"category": cat, "repo": mid, "released": made.date().isoformat(), "likes": m.get("likes") or 0,
                                "licence": lic, "licence_ok": ok == "ok", "size": size_note(m, cfg["gpu_gb"])})
                if mid in seen and not a.all:
                    continue
                rows.append((made, mid, m.get("likes") or 0, ok, lic, size_note(m, cfg["gpu_gb"])))
        rows.sort(reverse=True)
        for r in rows:
            new_seen[r[1]] = now.date().isoformat()
        total += len(rows)
        if rows:
            lines = [f"## {cat}", "", "| Model | Released | Likes | Licence | Size |", "|---|---|---|---|---|"]
            for made, mid, likes, ok, lic, size in rows:
                flag = "" if ok == "ok" else " (read it)"
                lines.append(f"| [{mid}](https://huggingface.co/{mid}) | {made.date()} | {likes} | {lic}{flag} | {size} |")
            sections.append("\n".join(lines))
    head = (f"# New open models worth a look, {now.date()}\n\n"
            f"Trending on Hugging Face, released in the last {cfg['max_age_days']} days, at least {cfg['min_likes']} likes, "
            "not already ours, not a copy of another model, and not barred from commercial use. "
            "\"(read it)\" = a custom licence: read it before testing. A model here is a candidate for a side-by-side test, not a pick.\n")
    body = "\n\n".join(sections) if sections else "Nothing new this week."
    report = head + "\n" + body + "\n"
    (out / "latest.md").write_text(report, encoding="utf-8")
    (out / f"{now.date()}.md").write_text(report, encoding="utf-8")
    # every current candidate (seen before or not), for pages that show the whole list
    (out / "latest.json").write_text(json.dumps({"date": now.date().isoformat(), "candidates": listing}, indent=1), encoding="utf-8")
    seen.update(new_seen)
    seen_f.write_text(json.dumps(seen, indent=1, sort_keys=True))
    print(f"{total} new candidate(s); report in {out / 'latest.md'}")


if __name__ == "__main__":
    main()
