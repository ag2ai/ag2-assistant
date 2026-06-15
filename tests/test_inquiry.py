"""HITL inquiries as durable, task-associated primitives."""

import asyncio

from agclaw.hitl.base import Question
from agclaw.hitl.inquiry import DurableAsker, Inquiry, InquiryStatus, InquiryStore
from agclaw.permissions import DENY
from agclaw.tasks import (
    DeliverableStatus,
    TaskManager,
    TaskStatus,
    TaskStore,
)


def _istore(tmp_path):
    return InquiryStore(path=tmp_path / "inq.db")


# ----- model -----

def test_inquiry_roundtrip_and_question_view():
    inq = Inquiry(id="inq-1", text="Audience?", options=["A", "B"], kind="question",
                  task_id="t1", detail="who for")
    again = Inquiry.from_dict({**inq.to_dict(), "bogus": 1})  # tolerates unknown keys
    assert again == inq
    q = inq.to_question()
    assert q.text == "Audience?" and q.options == ["A", "B"] and q.kind == "question"


# ----- store -----

async def test_store_create_answer_lifecycle(tmp_path):
    store = _istore(tmp_path)
    inq = await store.create("Proceed?", task_id="t1", options=["Yes", "No"])
    assert inq.status == InquiryStatus.PENDING
    assert [i.id for i in await store.list_pending("t1")] == [inq.id]
    done = await store.answer(inq.id, "Yes")
    assert done.status == InquiryStatus.ANSWERED and done.answer == "Yes"
    assert await store.list_pending("t1") == []
    # answering again is idempotent (first writer wins)
    assert (await store.answer(inq.id, "No")).answer == "Yes"


async def test_store_cancel_for_task(tmp_path):
    store = _istore(tmp_path)
    await store.create("q1", task_id="t1")
    await store.create("q2", task_id="t1")
    await store.create("q3", task_id="t2")
    n = await store.cancel_for_task("t1")
    assert n == 2
    assert await store.list_pending("t1") == []
    assert len(await store.list_pending("t2")) == 1


# ----- durable asker -----

class _ImmediateAsker:
    def __init__(self, answer):
        self.answer = answer
        self.asked = []

    async def ask(self, question, timeout=None):
        self.asked.append(question)
        return self.answer


class _BlockingAsker:
    async def ask(self, question, timeout=None):
        await asyncio.Event().wait()  # never returns on its own


async def test_durable_asker_records_live_answer(tmp_path):
    store = _istore(tmp_path)
    asker = DurableAsker(_ImmediateAsker("Execs"), store, task_id="t1")
    ans = await asker.ask(Question(text="Audience?"))
    assert ans == "Execs"
    inqs = await store.list_all()
    assert len(inqs) == 1
    assert inqs[0].task_id == "t1" and inqs[0].status == InquiryStatus.ANSWERED
    assert inqs[0].answer == "Execs"


async def test_durable_asker_resolves_out_of_band(tmp_path):
    """While the live transport blocks, an answer posted to the stored inquiry
    (e.g. from the GUI or another channel) resolves the ask."""
    store = _istore(tmp_path)
    asker = DurableAsker(_BlockingAsker(), store, task_id="t1")
    task = asyncio.ensure_future(asker.ask(Question(text="Where to?")))
    for _ in range(200):  # wait for the inquiry to be persisted
        pend = await store.list_pending("t1")
        if pend:
            break
        await asyncio.sleep(0.01)
    assert pend, "inquiry was never persisted"
    await store.answer(pend[0].id, "Lisbon")
    assert await asyncio.wait_for(task, timeout=2) == "Lisbon"


async def test_durable_asker_timeout_expires(tmp_path):
    store = _istore(tmp_path)
    asker = DurableAsker(_BlockingAsker(), store, timeout=0.2)
    # a plain question times out to "" ; a permission times out to DENY
    assert await asker.ask(Question(text="?", kind="question")) == ""
    assert await asker.ask(Question(text="ok?", kind="permission")) == DENY
    inqs = await store.list_all()
    assert all(i.status == InquiryStatus.EXPIRED for i in inqs)
    assert {i.kind for i in inqs} == {"question", "permission"}


async def test_durable_asker_rebind_tags_subtask(tmp_path):
    store = _istore(tmp_path)
    root = DurableAsker(_ImmediateAsker("x"), store, task_id="root")
    child = root.rebind("child")
    assert child.task_id == "child"
    await child.ask(Question(text="q"))
    assert (await store.list_all())[0].task_id == "child"


# ----- task integration -----

async def test_taskmanager_persists_task_inquiries(tmp_path):
    tstore = TaskStore(path=tmp_path / "t.db")
    istore = _istore(tmp_path)
    t = await tstore.create("ask then work")
    await tstore.add_deliverable(t.id, "out")
    seen = {}

    async def executor(task_id, mgr, asker):
        seen["ans"] = await asker.ask(Question(text="proceed?"))
        tk = await tstore.get(task_id)
        for d in tk.pending_deliverables():
            await tstore.set_deliverable_status(task_id, d["id"], DeliverableStatus.PRODUCED)

    mgr = TaskManager(tstore, executor, inquiry_store=istore)
    await mgr.submit(t.id, asker=_ImmediateAsker("go"))
    await mgr.wait(t.id)

    assert seen["ans"] == "go"
    inqs = await istore.list_all()
    assert len(inqs) == 1
    assert inqs[0].task_id == t.id and inqs[0].status == InquiryStatus.ANSWERED


async def test_taskmanager_cancel_releases_pending_inquiry(tmp_path):
    tstore = TaskStore(path=tmp_path / "t.db")
    istore = _istore(tmp_path)
    t = await tstore.create("blocks on a question")
    await tstore.add_deliverable(t.id, "out")

    async def executor(task_id, mgr, asker):
        await asker.ask(Question(text="waiting…"))  # blocks until answered/cancelled

    mgr = TaskManager(tstore, executor, inquiry_store=istore)
    await mgr.submit(t.id, asker=_BlockingAsker())
    for _ in range(200):
        if await istore.list_pending(t.id):
            break
        await asyncio.sleep(0.01)
    assert await istore.list_pending(t.id), "task never raised its inquiry"

    await mgr.cancel(t.id, reason="user stop")
    await mgr.wait(t.id)
    assert await istore.list_pending(t.id) == []  # released, not left dangling
    assert (await tstore.get(t.id)).status == TaskStatus.CANCELLED
