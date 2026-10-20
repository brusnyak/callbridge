"""Eval runner: replays scripted scenarios against the app, scores, writes SCOREBOARD.md.

Usage:
  python evals/runner.py                 # normal run, exit 1 if any scenario fails
  python evals/runner.py --fault NAME    # inject ONE fault into the app, exit 1 (FAIL) expected
  python evals/runner.py --negative      # run every fault; exit 0 only if EVERY fault is caught
Faults are injected into the app (not the inputs), so a blind grader would still pass them.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
SECRET = "eval-secret"
os.environ["CALLBRIDGE_SECRET"] = SECRET
os.environ["CALLBRIDGE_DB"] = ":memory:"
os.environ.pop("CALLBRIDGE_CRM_URL", None)  # never any network in evals

from fastapi.testclient import TestClient  # noqa: E402

import app.main as M  # noqa: E402
from app.db import latencies  # noqa: E402

FAULTS = ["skip-signature", "no-idempotency", "no-conflict-check",
          "wrong-tool-args", "no-guardrail", "trust-payload-booking"]


def sign(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


def load_scenarios() -> list[dict]:
    return json.loads((Path(__file__).parent / "scenarios.json").read_text())


def post(client, payload: dict, bad_sig: bool = False) -> dict:
    body = json.dumps(payload).encode()
    sig = "sha256=" + "0" * 64 if bad_sig else sign(body)
    r = client.post("/vapi/webhook", content=body,
                    headers={"X-Callbridge-Signature": sig,
                             "Content-Type": "application/json"})
    try:
        data = r.json()
    except Exception:
        data = {}
    return {"status": r.status_code, "body": data}


def inject(fault: str | None) -> None:
    """Break the app in one known way. Reload-free: patches module attributes."""
    if fault is None:
        return
    if fault == "skip-signature":
        M.verify = lambda secret, body, sig: True
    elif fault == "no-idempotency":
        M.store.seen_event = lambda eid: None
    elif fault == "no-conflict-check":
        orig = M.calendar.book

        def book(start_iso, minutes=30):
            M.calendar.busy.clear()  # forget existing bookings
            return orig(start_iso, minutes)
        M.calendar.book = book
    elif fault == "wrong-tool-args":
        orig = M.calendar.book

        def book(start_iso, minutes=30):  # books a different slot than asked
            ok, info = orig(start_iso, minutes)
            if ok:
                d = datetime.fromisoformat(info)
                return ok, d.replace(minute=30 if d.minute == 0 else 0).isoformat()
            return ok, info
        M.calendar.book = book
    elif fault == "no-guardrail":
        M.guardrail = lambda t: None
    elif fault == "trust-payload-booking":
        # report claims "booked" from payload alone (the original worker bug)
        M.store.booking_for_call = lambda cid: "bk_forged"
    else:
        raise SystemExit(f"unknown fault {fault}")


def same_instant(a: str, b: str) -> bool:
    try:
        return datetime.fromisoformat(a) == datetime.fromisoformat(b)
    except ValueError:
        return False


def grade(sc: dict, payload: dict, res: dict) -> tuple[bool, str]:
    exp = sc["expect"]
    if res["status"] != exp.get("status", 200):
        return False, f"status {res['status']} != {exp.get('status', 200)}"
    body = res["body"].get("result", res["body"])
    checks = []
    if "ok" in exp:
        checks.append((body.get("ok") is exp["ok"], f"ok={body.get('ok')}"))
    if exp.get("has_slots"):
        checks.append((len(body.get("slots", [])) > 0, "slots present"))
    if exp.get("no_slots"):
        checks.append((len(body.get("slots", [])) == 0, "no slots"))
    if exp.get("booked"):
        checks.append((bool(body.get("booking_id")), "booking_id present"))
    if exp.get("conflict"):
        checks.append(("conflict" in json.dumps(body).lower(), "conflict reported"))
    if exp.get("outcome_prefix"):
        checks.append(((body.get("outcome", "") or "").startswith(exp["outcome_prefix"]),
                       f"outcome={body.get('outcome')}"))
    bad = [d for ok, d in checks if not ok]
    return (not bad, "; ".join(d for _, d in checks) or "ok")


def run(fault: str | None = None) -> dict:
    saved = (M.verify, M.guardrail)
    try:
        return _run(fault)
    finally:
        M.verify, M.guardrail = saved  # faults must not leak into the next run


def _run(fault: str | None) -> dict:
    M.store = M.Store(":memory:")
    M.calendar = M.FakeCalendar()
    inject(fault)
    client = TestClient(M.app)
    results, lat_all = [], []
    for sc in load_scenarios():
        payload = json.loads(json.dumps(sc["payload"]))
        res = post(client, payload, bad_sig=sc.get("bad_sig", False))
        passed, detail = grade(sc, payload, res)
        dims: dict[str, bool] = {}
        body = res["body"].get("result", res["body"])
        if payload.get("tool", {}).get("name") == "book_appointment" and sc["expect"].get("booked"):
            want = payload["tool"]["args"]["start"]
            dims["tool_args"] = same_instant(want, body.get("start", ""))
        if sc["category"] == "guardrail":
            dims["guardrail"] = passed
        if sc["category"] == "auth":
            dims["auth"] = passed
        if sc.get("replay") and res["status"] == 200:
            res2 = post(client, payload)
            eid = payload["event"]["id"]
            n = M.store.conn.execute(
                "SELECT COUNT(*) c FROM bookings WHERE event_id=?", (eid,)).fetchone()["c"]
            leads = M.store.conn.execute(
                "SELECT COUNT(*) c FROM leads WHERE call_id=?",
                (payload.get("call", {}).get("id", ""),)).fetchone()["c"]
            dims["duplicate"] = res2["body"].get("duplicate") is True and n <= 1 and leads <= 1
            detail += f"; replay dup={res2['body'].get('duplicate')} bookings={n} leads={leads}"
        for k, v in dims.items():
            passed = passed and v
        lat_all += [float(x) for x in payload.get("latencies_ms", [])]
        results.append({"id": sc["id"], "category": sc["category"], "pass": passed,
                        "dims": dims, "detail": detail})
    return summarize(results, lat_all, fault)


def summarize(results, lat_all, fault) -> dict:
    by_cat: dict[str, dict] = {}
    dims: dict[str, dict] = {}
    for r in results:
        c = by_cat.setdefault(r["category"], {"pass": 0, "total": 0})
        c["total"] += 1
        c["pass"] += r["pass"]
        for k, v in r["dims"].items():
            d = dims.setdefault(k, {"pass": 0, "total": 0})
            d["total"] += 1
            d["pass"] += v
    p50, p95 = latencies(lat_all)
    total = len(results)
    passed = sum(r["pass"] for r in results)
    return {"fault": fault, "results": results, "by_category": by_cat, "dims": dims,
            "passed": passed, "total": total, "p50_ms": p50, "p95_ms": p95,
            "rate": round(100 * passed / total, 1) if total else 0}


def scoreboard_md(normal: dict, negs: list[dict]) -> str:
    L = ["# callbridge eval scoreboard", "",
         f"**Task success: {normal['passed']}/{normal['total']} ({normal['rate']}%)**", "",
         "| category | pass | total |", "|---|---|---|"]
    for cat, c in sorted(normal["by_category"].items()):
        L.append(f"| {cat} | {c['pass']} | {c['total']} |")
    L += ["", "| metric | pass | total |", "|---|---|---|"]
    for k, d in sorted(normal["dims"].items()):
        L.append(f"| {k} | {d['pass']} | {d['total']} |")
    L += ["", "(Latency is not reported: the scenario files carry recorded numbers, nothing is measured.)", "",
          "## Negative control (one fault injected into the app per run; must FAIL)", "",
          "| fault | scenarios passed | caught (run failed)? |", "|---|---|---|"]
    for n in negs:
        L.append(f"| {n['fault']} | {n['passed']}/{n['total']} | "
                 f"{'yes' if n['passed'] < n['total'] else 'NO - blind spot'} |")
    L += ["", "## Failed scenarios (normal run)", ""]
    fails = [r for r in normal["results"] if not r["pass"]]
    L += [f"- {r['id']} ({r['category']}): {r['detail']}" for r in fails] or ["- none"]
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fault", choices=FAULTS)
    ap.add_argument("--negative", action="store_true")
    a = ap.parse_args()
    if a.fault:
        s = run(a.fault)
        print(f"FAULT {a.fault}: {s['passed']}/{s['total']} passed")
        for r in s["results"]:
            if not r["pass"]:
                print("  FAIL:", r["id"], r["detail"])
        return 0 if s["passed"] == s["total"] else 1
    normal = run(None)
    print(f"NORMAL: {normal['passed']}/{normal['total']} ({normal['rate']}%) "
          f"dims={ {k: f'{d['pass']}/{d['total']}' for k, d in normal['dims'].items()} }")
    for r in normal["results"]:
        if not r["pass"]:
            print("  FAIL:", r["id"], r["detail"])
    negs = [run(f) for f in FAULTS]
    for n in negs:
        print(f"NEGATIVE {n['fault']}: {n['passed']}/{n['total']} -> "
              f"{'caught' if n['passed'] < n['total'] else 'BLIND SPOT'}")
    (Path(__file__).parent / "SCOREBOARD.md").write_text(scoreboard_md(normal, negs))
    blind = [n["fault"] for n in negs if n["passed"] == n["total"]]
    if a.negative:
        return 1 if blind else 0
    return 0 if normal["passed"] == normal["total"] and not blind else 1


if __name__ == "__main__":
    sys.exit(main())
