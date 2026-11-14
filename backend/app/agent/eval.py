"""Question evaluation set: does the agent pick the right tool, the right item/supplier,
and come back with the expected facts?

    python -m app.agent.eval              # rules mode (no LLM, free)
    python -m app.agent.eval --mode llm   # live LLM (uses API quota)
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from app.agent.envelope import Envelope

QUESTIONS = Path(__file__).with_name("eval_questions.json")


def load() -> list[dict]:
    return json.loads(QUESTIONS.read_text(encoding="utf-8"))


def _entity(ev, key: str):
    r = ev.resolved.get(key, {})
    return r.get("id") or r.get("name") or ev.args.get(key)


def grade(case: dict, env: Envelope) -> tuple[bool, str]:
    evs = [e for e in env.evidence if not e.error]
    wanted = case["tool"] if isinstance(case["tool"], list) else [case["tool"]]
    match = next((e for e in evs if e.tool in wanted), None)
    if match is not None and match.tool != wanted[0]:  # an accepted alternative: skip arg checks
        return (bool(env.answer_md.strip()) and not env.grounding.ungrounded,
                "ok (alternative tool)" if not env.grounding.ungrounded else
                f"ungrounded numbers {env.grounding.ungrounded}")
    if match is None:
        return False, f"tool {[e.tool for e in env.evidence] or 'none'} != {case['tool']}"
    row_ids = {r.get("item_id") for r in match.rows}
    for k, want in case.get("entities", {}).items():
        got = _entity(match, k)
        # an unfiltered answer that still covers the item (e.g. both attas) is fine
        if got != want and not (got is None and k == "item" and want in row_ids):
            return False, f"{k}={got!r} != {want!r}"
    for k, want in case.get("args", {}).items():
        if match.args.get(k) != want:
            return False, f"arg {k}={match.args.get(k)!r} != {want!r}"
    chk = case.get("check", {})
    for k, want in chk.get("headline", {}).items():
        if match.headline.get(k) != want:
            return False, f"headline {k}={match.headline.get(k)!r} != {want!r}"
    if "rows_include" in chk:
        ids = {r.get("item_id") for r in match.rows}
        missing = set(chk["rows_include"]) - ids
        if missing:
            return False, f"rows missing {sorted(missing)}"
    if "confidence" in case and match.confidence != case["confidence"]:
        return False, f"confidence {match.confidence} != {case['confidence']}"
    if not env.answer_md.strip():
        return False, "empty answer"
    if env.grounding.ungrounded:
        return False, f"ungrounded numbers {env.grounding.ungrounded}"
    return True, "ok"


def run(agent, mode: str = "rules", pause_s: float = 0.0) -> dict:
    results = []
    for case in load():
        if case.get("llm_only") and mode == "rules":
            continue
        env = agent.ask(case["q"], mode=mode)
        ok, why = grade(case, env)
        results.append({"q": case["q"], "ok": ok, "why": why, "mode": env.mode,
                        "model": env.model, "latency_ms": env.latency_ms,
                        "answer": env.answer_md})
        if pause_s:
            time.sleep(pause_s)
    passed = sum(r["ok"] for r in results)
    return {"passed": passed, "total": len(results), "results": results}


def main() -> None:
    from app.agent.core import Agent
    from app.agent.llm import build_backend
    from app.config import get_settings
    from app.store.manager import DataManager

    mode = sys.argv[sys.argv.index("--mode") + 1] if "--mode" in sys.argv else "rules"
    s = get_settings()
    m = DataManager(s)
    m.startup()
    agent = Agent(m, build_backend(s) if mode != "rules" else None)
    rep = run(agent, mode, pause_s=1.0 if mode != "rules" else 0)
    for r in rep["results"]:
        mark = "PASS" if r["ok"] else "FAIL"
        print(f"{mark} [{r['mode']}{'/' + r['model'] if r['model'] else ''} {r['latency_ms']}ms] "
              f"{r['q']}\n      -> {r['answer'][:160]}" + ("" if r["ok"] else f"\n      !! {r['why']}"))
    print(f"\n{rep['passed']}/{rep['total']} passed")


if __name__ == "__main__":
    main()
