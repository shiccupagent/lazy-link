#!/usr/bin/env python3
"""watch.py - list new open image models worth a test.

  watch.py            new candidates since the last check: Comfy-Org repacks (ComfyUI runs these natively)
                      + trending text-to-image models on Hugging Face, with licence and ComfyUI support
  watch.py --mark     also record them as seen in watch-seen.json (next to this file, git-ignored), so the next
                      check shows only newer ones

Reads only public Hugging Face API pages: no key, no token, no account. Stdlib only. If Hugging Face answers
"too many requests", wait a few minutes and run it again.

A candidate gets tested with your own small set of asks (judge the pictures by eye) against the default model:
  add it to models.json (official URL, size, sha256, licence) -> imagegen.py fetch --models KEY
  -> imagegen.py run YOUR_ASKS.json --models krea2,KEY --out DIR -> sheet.py to compare side by side
"""
import json, sys, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SEEN = HERE / "watch-seen.json"
API = "https://huggingface.co/api/models"
NOISE = ("lora", "gguf", "uncensored", "quant", "nsfw", "-fp8", "-int8", "-nf4", "awq", "merge", "test")


def get(url):
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.load(r)


def licence(repo):
    try:
        d = get(f"{API}/{repo}")
    except Exception:
        return "?", []
    tags = [t[len("license:"):] for t in d.get("tags", []) if t.startswith("license:")]
    card = d.get("cardData") or {}
    name = card.get("license_name") or (tags[0] if tags else "?")
    return name, (card.get("base_model") or [])


def main():
    if "-h" in sys.argv or "--help" in sys.argv:
        print(__doc__)
        return
    seen = json.load(open(SEEN)) if SEEN.exists() else {"ids": []}
    known = set(seen["ids"])
    comfy = [m for m in get(f"{API}?author=Comfy-Org&sort=createdAt&direction=-1&limit=40")]
    image_comfy = []
    for m in comfy:
        d = get(f"{API}/{m['id']}")
        if any(t in d.get("tags", []) for t in ("text-to-image", "image-to-image")) or d.get("pipeline_tag") in ("text-to-image", "image-to-image") \
                or any(w in m["id"].lower() for w in ("image", "flux", "krea", "fibo", "kolors", "chroma")):
            image_comfy.append(m["id"])
    trending = [m for m in get(f"{API}?pipeline_tag=text-to-image&sort=trendingScore&limit=40")
                if not any(w in m["id"].lower() for w in NOISE)]
    rows = []
    for repo in image_comfy:
        lic, base = licence(repo)
        rows.append((repo, "yes (Comfy-Org repack)", lic, ", ".join(base) if isinstance(base, list) else str(base)))
    for m in trending:
        if m["id"].startswith("Comfy-Org/"):
            continue
        lic, _ = licence(m["id"])
        rows.append((m["id"], "repack?" if not any(m["id"].split("/")[1].lower()[:6] in r.lower() for r in image_comfy) else "yes", lic, m.get("createdAt", "")[:10]))
    new = [r for r in rows if r[0] not in known]
    print(f"{len(new)} new of {len(rows)} candidates (seen list: {len(known)})")
    for repo, comfy_ok, lic, note in new:
        print(f"  {repo:52} comfy: {comfy_ok:22} licence: {lic:28} {note}")
    if "--mark" in sys.argv:
        seen["ids"] = sorted(known | {r[0] for r in rows})
        json.dump(seen, open(SEEN, "w"), indent=1)
        print(f"marked as seen -> {SEEN.name}")


if __name__ == "__main__":
    main()
