"""check_pc.py ROUND | join - runs on the PC (voice/speak.py sends it): ROUND in Whisper's venv, join in Chatterbox's.
ROUND listens to that round's attempts (att/<key>_<round>.wav), keeps each sentence's best attempt so far in best.json (how closely Whisper's words
match the text) and writes todo.json: the sentences still below GOOD. `join` puts each line's best sentences
together into $RENDER_OUT/<id>.wav and writes $RENDER_OUT/report.json."""
import difflib, json, os, re, sys

GOOD = 0.9          # character match of Whisper's words vs the text, used to keep the best attempt
# a sentence passes only when every word was heard as written: 0.9 still let a stray "seven" and "Barbie's" for
# "Barnaby's" through (the tutorial video's narration)
GAP = 0.3           # seconds between sentences of one line
ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def spell(n):
    if n < 20: return ONES[n]
    if n < 100: return TENS[n // 10] + ("" if n % 10 == 0 else " " + ONES[n % 10])
    if n < 1000: return ONES[n // 100] + " hundred" + ("" if n % 100 == 0 else " " + spell(n % 100))
    if n < 1000000: return spell(n // 1000) + " thousand" + ("" if n % 1000 == 0 else " " + spell(n % 1000))
    return str(n)


def norm(s):
    """letters only, numbers spelled out, so '12 GB' and 'twelve gigabytes' compare on what was said"""
    s = re.sub(r"(\d+)(st|nd|rd|th)\b", r"\1", s.lower())
    s = re.sub(r"\d+", lambda m: " " + spell(int(m.group())) + " ", s)
    return re.sub(r"[^a-z]", "", s)


def words(s):
    """the words as said: numbers spelled out first, so '500' is the two words 'five hundred'"""
    s = re.sub(r"(\d+)(st|nd|rd|th)\b", r"\1", s.lower())
    s = re.sub(r"\d+", lambda m: " " + spell(int(m.group())) + " ", s)
    return [w for w in (re.sub(r"[^a-z]", "", x) for x in s.split()) if w]


def check(rnd):
    jobs = {j["key"]: j for j in json.load(open("sentences.json"))}
    best = json.load(open("best.json")) if os.path.exists("best.json") else {}
    todo = json.load(open("todo.json")) if os.path.exists("todo.json") else list(jobs)
    from faster_whisper import WhisperModel
    m = WhisperModel(os.path.join(os.environ["RENDER_ROOT"], "models", "whisper-large-v3"), device="cuda", compute_type="float16")
    for key in todo:
        path = os.path.join("att", f"{key}_{rnd}.wav")
        segs, _ = m.transcribe(path, language="en", beam_size=5)
        said = " ".join(s.text.strip() for s in segs)
        score = difflib.SequenceMatcher(None, norm(jobs[key]["text"]), norm(said)).ratio()
        if key not in best or score > best[key]["score"]:
            best[key] = {"round": rnd, "score": round(score, 3), "said": said,
                         "exact": words(jobs[key]["text"]) == words(said)}
    todo = [k for k in jobs if not best[k].get("exact")]
    json.dump(best, open("best.json", "w"), indent=1)
    json.dump(todo, open("todo.json", "w"))
    print(f"round {rnd}: {len(jobs) - len(todo)} of {len(jobs)} sentences match, {len(todo)} to say again", flush=True)


def join():
    import numpy as np, soundfile as sf
    jobs = {j["key"]: j for j in json.load(open("sentences.json"))}
    best = json.load(open("best.json"))
    out, lines, report = os.environ["RENDER_OUT"], {}, []
    for k, j in jobs.items():
        lines.setdefault(j["id"], []).append(k)
    for lid, keys in lines.items():
        parts, sr = [], None
        for k in keys:
            a, sr = sf.read(os.path.join("att", f"{k}_{best[k]['round']}.wav"), dtype="float32")
            parts += [a, np.zeros(int(sr * GAP), np.float32)]
            report.append({"key": k, "text": jobs[k]["text"], **best[k]})
        sf.write(os.path.join(out, lid + ".wav"), np.concatenate(parts[:-1]), sr)
    json.dump(report, open(os.path.join(out, "report.json"), "w"), indent=1)
    low = [r for r in report if not r.get("exact")]
    print(f"joined {len(lines)} lines; {len(low)} sentences not heard word for word (best attempt kept)", flush=True)


join() if sys.argv[1] == "join" else check(int(sys.argv[1]))
