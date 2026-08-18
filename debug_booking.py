"""Debug: show AI replies for the booking scenario."""
import asyncio, os, sys, sqlite3

os.environ["BUSINESS_TYPE"] = "plumbing"
os.environ["BUSINESS_NAME"] = "Helena Plumbing Co"
os.environ["SERVICE_AREA"] = "Helena, MT"
sys.path.insert(0, r"C:\Users\Sevin\missed-call-ai")
import app
from app import inbound_sms

TEST_PHONE = "+14069993322"
SENT = []
def fake_send(to, body, from_number=None):
    SENT.append((to, body))
    return f"fake-{len(SENT)}"
app.send_sms = fake_send

DB = r"C:\Users\Sevin\missed-call-ai\data\conversations.db"

async def main():
    db = sqlite3.connect(DB)
    db.execute("DELETE FROM scheduled_tasks WHERE phone_number=?", (TEST_PHONE,))
    db.execute("DELETE FROM messages WHERE conversation_id IN (SELECT id FROM conversations WHERE phone_number=?)", (TEST_PHONE,))
    db.execute("DELETE FROM conversations WHERE phone_number=?", (TEST_PHONE,))
    db.commit(); db.close()

    turns = [
        "my sink is clogged, what's the price to come out?",
        "1234 fake st please",
        "Wednesday August 19th at 3pm works, name is Test Customer",
    ]
    for t in turns:
        await inbound_sms(From=TEST_PHONE, Body=t)
        print(f"\nUSER: {t}")
        if SENT:
            print(f"AI:   {SENT[-1][1][:200]}")

    db = sqlite3.connect(DB); db.row_factory = sqlite3.Row
    convs = db.execute("SELECT id, state, branch, metadata_json FROM conversations WHERE phone_number=?", (TEST_PHONE,)).fetchall()
    for c in convs:
        print(f"\nconv #{c['id']} state={c['state']} branch={c['branch']} meta={c['metadata_json']}")
    db.close()

if __name__ == "__main__":
    asyncio.run(main())
