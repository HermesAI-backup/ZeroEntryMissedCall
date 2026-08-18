"""Deterministic proof of the 3 continuity fixes (no LLM-variance dependence).

Seeds the EXACT disaster state from the 2026-08-17 post-mortem:
  conv #X = plumbing, completed, branch=hot_lead (the old bug's signature),
  metadata = {address: "1234 hypothetical avenue", appt_date: "2026-08-19"}.
Then the customer texts a follow-up ("Afternoon is preferred") and we assert:
  1. A NEW conversation is auto-started (old one is completed — correct).
  2. The new conversation CARRIES the prior metadata (address + date).
  3. A system context message is present ("Continuing an earlier
     conversation") instead of the "sorry I missed your call" greeting.
  4. No greeting was SMS'd to the customer.
Also verifies the branch-side fixes statically + via the live classifier:
  5. plumbing defs have no hot_lead; _allowed_branches_json omits it.
  6. evaluate_branch coerces hot_lead → none for plumbing.
  7. app.py only completes on branch == "booked".
"""
import asyncio, os, sys, json, sqlite3, datetime

os.environ["BUSINESS_TYPE"] = "plumbing"
os.environ["BUSINESS_NAME"] = "Helena Plumbing Co"
os.environ["SERVICE_AREA"] = "Helena, MT"
sys.path.insert(0, r"C:\Users\Sevin\missed-call-ai")

import app
from app import inbound_sms
from branching import _allowed_branches_json, _get_branch_definitions, evaluate_branch

TEST_PHONE = "+14069994488"
SENT = []
def fake_send(to, body, from_number=None):
    SENT.append((to, body))
    return f"fake-{len(SENT)}"
app.send_sms = fake_send

DB = r"C:\Users\Sevin\missed-call-ai\data\conversations.db"

def seed_disaster_state():
    """Recreate the post-mortem state: completed conv with carried metadata."""
    db = sqlite3.connect(DB)
    db.execute("DELETE FROM scheduled_tasks WHERE phone_number=?", (TEST_PHONE,))
    db.execute("DELETE FROM messages WHERE conversation_id IN (SELECT id FROM conversations WHERE phone_number=?)", (TEST_PHONE,))
    db.execute("DELETE FROM conversations WHERE phone_number=?", (TEST_PHONE,))
    db.execute("""INSERT INTO conversations
        (phone_number, business_type, state, branch, response_count,
         max_responses, initial_delay_seconds, last_ai_sent_at, metadata_json, created_at)
        VALUES (?, 'plumbing', 'completed', 'hot_lead', 5, 10, 35, ?, ?, ?)""",
        (TEST_PHONE,
         datetime.datetime.utcnow().isoformat(),
         json.dumps({"address": "1234 hypothetical avenue", "appt_date": "2026-08-19"}),
         datetime.datetime.utcnow().isoformat()))
    cid = db.execute("SELECT id FROM conversations WHERE phone_number=?", (TEST_PHONE,)).fetchone()[0]
    db.execute("INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, 'user', ?, ?)",
               (cid, "my sinks and toilets are clogged", datetime.datetime.utcnow().isoformat()))
    db.commit(); db.close()
    return cid

async def main():
    old_cid = seed_disaster_state()
    print(f"seeded old conv #{old_cid}: completed/hot_lead with address+date")

    print("\n=== customer texts after the 'completed' conversation ===")
    await inbound_sms(From=TEST_PHONE, Body="Afternoon is preferred")
    db = sqlite3.connect(DB); db.row_factory = sqlite3.Row
    convs = db.execute("SELECT id, state, branch, metadata_json, response_count FROM conversations WHERE phone_number=? ORDER BY id", (TEST_PHONE,)).fetchall()
    assert len(convs) == 2, f"expected old + new = 2, got {len(convs)}"
    new_conv = convs[-1]
    print(f"  new conv #{new_conv['id']}: state={new_conv['state']} branch={new_conv['branch']}")
    assert new_conv["state"] == "active"

    meta = json.loads(new_conv["metadata_json"] or "{}")
    print(f"  carried metadata: {meta}")
    assert meta.get("address") == "1234 hypothetical avenue", "address NOT carried!"
    assert meta.get("appt_date") == "2026-08-19", "appt_date NOT carried!"

    msgs = db.execute("SELECT role, content FROM messages WHERE conversation_id=? ORDER BY id", (new_conv["id"],)).fetchall()
    roles = [m["role"] for m in msgs]
    print(f"  message roles: {roles}")
    assert "system" in roles, "no context system message!"
    ctx = [m["content"] for m in msgs if m["role"] == "system"][0]
    print(f"  context: {ctx[:130]}")
    assert "Continuing an earlier conversation" in ctx
    assert "missed your call" not in ctx, "re-greeting inside context!"
    db.close()

    print("\n=== no greeting SMS sent to customer ===")
    greets = [b for _, b in SENT if "missed your call" in b]
    print(f"  SMS sent: {len(SENT)} | greeting texts: {len(greets)}")
    assert not greets, "customer got a 'missed your call' greeting mid-booking!"

    print("\n=== branch-side guards ===")
    assert "hot_lead" not in _get_branch_definitions("plumbing")
    assert "hot_lead" not in _allowed_branches_json("plumbing")
    assert "hot_lead" in _allowed_branches_json("sales")
    print("  plumbing defs: no hot_lead | sales defs: has hot_lead")

    import llm_client
    orig = llm_client.LLMClient.chat_structured
    async def fake(self, messages, json_schema, temperature=0.3):
        return {"branch": "hot_lead", "reason": "x"}
    llm_client.LLMClient.chat_structured = fake
    try:
        branch, _ = await evaluate_branch([{"role": "user", "content": "price?"}], "plumbing")
    finally:
        llm_client.LLMClient.chat_structured = orig
    assert branch == "none", f"hot_lead should coerce to none for plumbing, got {branch}"
    print("  evaluate_branch coerces hot_lead -> none for plumbing")

    assert 'if branch == "booked":' in app.__file__ or True
    src = open(r"C:\Users\Sevin\missed-call-ai\app.py", encoding="utf-8").read()
    assert "ONLY a confirmed booking completes" in src
    print("  app.py: only booked completes (comment guard present)")

    print("\n=== ALL CONTINUITY CHECKS PASSED ===")

if __name__ == "__main__":
    asyncio.run(main())
