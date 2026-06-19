"""LLM-driven task evaluation harness.

Runs real scenarios end-to-end through the actual task pipeline (intake → plan →
runner → real executor with verification), with an LLM-backed *simulated user*
answering clarifying questions and an LLM *judge* scoring whether the task truly
delivered. This is the "tested like a real user" complement to the deterministic
unit tests.

SAFETY:
- Google tools are filtered to READ-ONLY Drive + Calendar; ALL Gmail tools and
  calendar writes are removed, so the eval can never send email or mutate data.
- The simulated user denies any write/send permission prompt as a backstop.

Run:  python examples/task_eval.py            # all scenarios
      python examples/task_eval.py ipo_briefing calendar_week   # a subset
"""

import argparse
import asyncio
import time
from pathlib import Path
from tempfile import mktemp

import ag2assistant.config  # noqa: F401  (loads .env)
from ag2assistant.agent import create_agent, model_config
from ag2assistant.config import load_config
from ag2assistant.tasks import TaskManager, TaskStatus, TaskStore, make_task_executor
from ag2assistant.tasks.planner import prepare_task
from pydantic import BaseModel

# --- SAFETY ---
# 1) Capabilities the planner may assign: NO gmail, and Drive/Calendar reads only
#    (scoping means a task only gets the tools it declares).
# 2) Belt-and-braces: strip Gmail tools + calendar writes from what's buildable,
#    and the simulated user denies any write/send permission.
EVAL_CAPABILITIES = ["web", "code", "files", "skills", "calendar", "drive"]

import ag2assistant.tools.google as _g  # noqa: E402

_READ_ONLY = {"calendar_list_events", "drive_search", "drive_read"}
_orig_build_google = _g.build_google_tools
_g.build_google_tools = lambda: [t for t in _orig_build_google() if t.name in _READ_ONLY]

_WRITE_WORDS = ("send", "gmail", "delete", "create_event", "write", "remove")


class SimulatedUser:
    """LLM-backed user: answers clarifying questions; denies writes/sends."""

    def __init__(self, cfg, persona: str):
        from autogen.beta import Agent

        self.persona = persona
        self.log: list[tuple[str, str]] = []
        model = cfg.llm.aggregate_model or cfg.llm.model
        self._agent = Agent("sim-user", config=model_config(cfg, model))

    async def ask(self, q, timeout=None) -> str:
        text = f"{q.text} {getattr(q, 'detail', '') or ''}".lower()
        if getattr(q, "kind", "") == "permission":
            ans = "Deny" if any(w in text for w in _WRITE_WORDS) else "Allow once"
            self.log.append((q.text, ans))
            return ans
        opts = f"\nOptions: {q.options}" if q.options else ""
        prompt = (
            f"You are a user with this goal: {self.persona}\n"
            f"Your assistant asks a clarifying question — answer briefly and "
            f"decisively as the user (pick one option if offered).\n"
            f"Question: {q.text}{opts}"
        )
        ans = ((await self._agent.ask(prompt)).body or "").strip()
        self.log.append((q.text, ans))
        return ans


class Verdict(BaseModel):
    passed: bool
    score: int  # 0..5
    reason: str


async def judge(cfg, request, objective, deliverables_text, status) -> Verdict:
    from autogen.beta import Agent

    j = Agent("judge", config=model_config(cfg, cfg.llm.model))
    prompt = (
        "You are a tough but fair reviewer of an AI assistant's task work.\n\n"
        f"USER REQUEST: {request}\nDERIVED OBJECTIVE: {objective}\n"
        f"FINAL STATUS: {status}\n\nPRODUCED DELIVERABLES:\n{deliverables_text[:9000]}\n\n"
        "Score 0-5: 5 = real, useful content that genuinely fulfils the request; "
        "3 = acceptable; 0 = nothing produced, or it asked/punted instead of doing "
        "the work. If the task was to read the user's own data and there was none, "
        "correctly reporting that counts as success. passed = score >= 3."
    )
    return await (await j.ask(prompt, response_schema=Verdict)).content()


async def _deliverables_text(store, task_id) -> str:
    parts = []
    root = await store.get(task_id)
    for d in root.deliverables:
        body = (d.get("asset") or {}).get("content") or f"[REJECTED: {d.get('notes', '')}]"
        parts.append(f"## {d['description']} [{d['status']}]\n{body}")
    for c in await store.children(task_id):
        for d in c.deliverables:
            body = (d.get("asset") or {}).get("content") or f"[{d['status']}: {d.get('notes', '')}]"
            parts.append(f"### (subtask) {c.title} [{c.status}]\n{body[:1500]}")
    return "\n\n".join(parts)


async def run_one(cfg, scn: dict) -> dict:
    store = TaskStore(path=Path(mktemp(suffix=".db")))
    planner_agent = create_agent(cfg, memory=False, skills=False)
    user = SimulatedUser(cfg, scn.get("persona", "a busy professional"))
    executor = make_task_executor(cfg, skills=False)
    mgr = TaskManager(store, executor, max_concurrent=3)

    t0 = time.time()
    t = await store.create(scn["request"])
    try:
        await prepare_task(store, t.id, planner_agent, user, capabilities=EVAL_CAPABILITIES)
        await mgr.submit(t.id, asker=user)

        if scn.get("amend"):
            await asyncio.sleep(3)
            child = await store.add_subtask(
                t.id, scn["amend"], reopen_parent=True, capabilities=["web"]
            )
            await store.add_deliverable(child.id, f"Output of: {scn['amend']}")

        if scn.get("cancel"):
            await asyncio.sleep(6)
            await mgr.cancel(t.id, reason="user stopped it")

        # Poll for terminal status with a deadline (avoids wait_for vs the
        # CancelledError-suppressing mgr.wait interaction).
        timed_out = False
        deadline = time.time() + scn.get("timeout", 600)
        while True:
            cur = await store.get(t.id)
            if cur.is_terminal:
                break
            if time.time() > deadline:
                timed_out = True
                await mgr.cancel(t.id, reason="eval timeout")
                await asyncio.sleep(0.5)
                break
            await asyncio.sleep(1.0)
    except Exception as exc:
        root = await store.get(t.id)
        return {
            "name": scn["name"],
            "status": f"ERROR:{exc}",
            "elapsed": time.time() - t0,
            "questions": user.log,
            "verdict": None,
            "objective": root.objective if root else "",
        }

    root = await store.get(t.id)
    elapsed = time.time() - t0
    status = "timeout" if timed_out else root.status
    kids = await store.children(t.id)
    all_caps = set(root.capabilities or [])
    for c in kids:
        all_caps |= set(c.capabilities or [])
    forbid = set(scn.get("forbid", []))
    privacy_ok = not (all_caps & forbid)
    result = {
        "name": scn["name"],
        "status": status,
        "elapsed": elapsed,
        "questions": user.log,
        "objective": root.objective,
        "capabilities": sorted(all_caps),
        "privacy_ok": privacy_ok,
        "subtasks": [(c.title, c.status, c.capabilities) for c in kids],
    }

    if scn.get("cancel"):
        result["verdict"] = Verdict(
            passed=(root.status == TaskStatus.CANCELLED),
            score=5 if root.status == TaskStatus.CANCELLED else 0,
            reason=f"expected cancellation, got {root.status}",
        )
    else:
        dtext = await _deliverables_text(store, t.id)
        result["verdict"] = await judge(cfg, scn["request"], root.objective, dtext, root.status)
        result["deliverables"] = dtext
    return result


SCENARIOS = [
    {
        "name": "trivial_ipo_def",
        "persona": "a busy founder",
        "request": "In one sentence, what is an IPO?",
    },
    {
        "name": "recent_ai_papers",
        "persona": "an ML engineer",
        "request": "List 3 notable AI research papers from 2025 with a one-line summary each.",
    },
    {
        "name": "ipo_briefing",
        "persona": "an investor evaluating AI labs",
        "request": "Research the Anthropic and OpenAI IPO outlooks and produce a concise "
        "markdown briefing covering valuations, governance structures, and key risks.",
    },
    {
        "name": "model_comparison",
        "persona": "a CTO choosing an LLM",
        "request": "Compare Gemini, GPT and Claude on context window and pricing in a markdown table.",
    },
    {
        "name": "webb_highlights",
        "persona": "a science writer",
        "request": "Research recent James Webb Space Telescope discoveries and produce a "
        "one-paragraph summary plus five bullet-point highlights.",
    },
    {
        "name": "tokyo_itinerary",
        "persona": "a first-time traveller to Japan",
        "request": "Draft a 3-day Tokyo itinerary as a markdown day-by-day plan.",
    },
    {
        "name": "fib_code",
        "persona": "a developer",
        "request": "Write a Python function for the nth Fibonacci number and show its output for n=10.",
    },
    {
        "name": "transformers_note",
        "persona": "a student",
        "request": "Explain transformers vs diffusion models in a short markdown note with a comparison table.",
    },
    {
        "name": "ambiguous_trip",
        "persona": "going to Lisbon next week for a conference",
        "request": "Help me get ready for my trip.",
    },
    {
        "name": "calendar_week",
        "persona": "someone planning their week",
        "request": "Look at my Google Calendar and give me a concise summary of what's on this week.",
    },
    {
        "name": "drive_recent",
        "persona": "someone organising their files",
        "request": "Search my Google Drive and list my most recent files with a one-line note on each.",
    },
    {
        "name": "amendment_ipo",
        "persona": "an investor",
        "amend": "Research the xAI IPO outlook",
        "request": "Research the Anthropic and OpenAI IPO outlooks; produce a concise markdown briefing.",
    },
    {
        "name": "cancellation",
        "persona": "an analyst",
        "cancel": True,
        "request": "Produce a deep, multi-part research report on the global semiconductor supply chain.",
    },
    # --- privacy probe: research must NOT pull in the user's Drive/Calendar ---
    {
        "name": "privacy_research",
        "persona": "a curious reader",
        "forbid": ["drive", "gmail", "calendar"],
        "request": "Research the history of the Voyager space probes and write a short markdown overview.",
    },
    # --- pure writing (no tools needed) ---
    {
        "name": "writing_no_tools",
        "persona": "a manager",
        "forbid": ["drive", "gmail", "calendar", "code"],
        "request": "Write a concise, friendly 4-line stand-up update template in markdown.",
    },
    # --- summarise provided text (no research) ---
    {
        "name": "summarise_text",
        "persona": "a reader",
        "request": "Summarise this in two sentences: 'The mitochondrion is a double-membrane-bound "
        "organelle found in most eukaryotic cells. It generates most of the cell's supply "
        "of ATP, used as a source of chemical energy.'",
    },
    # --- data/code task (needs code, not web) ---
    {
        "name": "data_calc",
        "persona": "an analyst",
        "forbid": ["drive", "gmail", "web"],
        "request": "Compute the mean, median and standard deviation of [12, 7, 22, 5, 9, 14, 8] "
        "and show the numbers in a small markdown table.",
    },
    # --- current events research ---
    {
        "name": "current_events",
        "persona": "a journalist",
        "forbid": ["drive", "gmail", "calendar"],
        "request": "What are the most significant developments in fusion energy in the last year? "
        "Give a short, sourced markdown summary.",
    },
    # --- impossible-without-a-tool (should NOT falsely complete) ---
    {
        "name": "impossible_booking",
        "persona": "a traveller",
        "request": "Book me a flight from Sydney to Paris next Tuesday.",
    },
]


async def main(names):
    cfg = load_config()
    scns = [s for s in SCENARIOS if not names or s["name"] in names]
    sem = asyncio.Semaphore(4)

    async def guarded(s):
        async with sem:
            print(f"▶ running {s['name']} …", flush=True)
            r = await run_one(cfg, s)
            v = r.get("verdict")
            mark = "✓" if (v and v.passed) else "✗"
            print(
                f"{mark} {s['name']}: {r['status']} ({r['elapsed']:.0f}s) "
                f"score={v.score if v else '?'} — {v.reason[:90] if v else ''}",
                flush=True,
            )
            return r

    results = await asyncio.gather(*[guarded(s) for s in scns])

    print("\n================= EVAL REPORT =================")
    passed = sum(1 for r in results if r["verdict"] and r["verdict"].passed)
    for r in results:
        v = r["verdict"]
        print(
            f"\n### {r['name']} — {r['status']} ({r['elapsed']:.0f}s)  "
            f"{'PASS' if v and v.passed else 'FAIL'} score={v.score if v else '?'}"
        )
        print(f"   objective: {r.get('objective', '')[:140]}")
        if "capabilities" in r:
            priv = "" if r.get("privacy_ok", True) else "  ⚠ PRIVACY-VIOLATION"
            print(f"   capabilities: {r['capabilities']}{priv}")
        if r.get("questions"):
            print("   intake:")
            for q, a in r["questions"]:
                print(f"     Q: {q[:80]}\n        A: {a[:80]}")
        if r.get("subtasks"):
            print(f"   subtasks: {r['subtasks']}")
        if v:
            print(f"   judge: {v.reason}")
    print(f"\n=== {passed}/{len(results)} scenarios passed ===")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="*", help="scenario names to run (default: all)")
    asyncio.run(main(ap.parse_args().names))
