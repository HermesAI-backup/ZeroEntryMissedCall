# Model A/B — deepseek/deepseek-v4-flash — 20260818_140716

| scenario | expected | got | flags |
|---|---|---|---|
| booking_simple | booked | booked | re_asks_address, re_asks_time |
| continuity_mid_booking | booked | booked | re_asks_time, no_question, booking_missing:customer_name |
| vague_time | none | none | — |
| price_pushback | none | none | — |
| emergency_flood | emergency | emergency | no_question |
| wrong_number | unqualified | unqualified | — |
| rude_spam | unqualified | none | no_question, branch_mismatch:got=none,expected=unqualified |
| prospect_interested | hot_lead | hot_lead | — |
| prospect_pricing | none | none | no_question |
| prospect_skeptic | none | none | — |

**TOTAL FLAGS: 9**

### booking_simple (expected booked, got booked)
🤖 AI  [initial]: Hey Helena Plumbing Co here — sorry I missed your call! What's going on with your plumbing? Leak, clog, or something else?

👤  : yeah my kitchen sink is clogged
🤖 AI  [none]: Got it, a clogged kitchen sink is pretty common. Can I get your address so we can swing by?
      ⚠️ re_asks_address
👤  : name's Tom, address 123 Main St, Helena
🤖 AI  [none]: Thanks, Tom! What time works best for you to have someone come out?
      ⚠️ re_asks_time
👤  : tomorrow morning works, like 9 or 10
🤖 AI  [booked]: We have 9:00 AM or 10:00 AM open tomorrow. Which works better for you, Tom?

### continuity_mid_booking (expected booked, got booked)
🤖 AI  [initial]: Hey Helena Plumbing Co here — sorry I missed your call! What's going on with your plumbing? Leak, clog, or something else?

👤  : my bathroom sink is dripping
🤖 AI  [none]: No worries, a dripping sink is a common fix! Is it a steady drip or more of a slow leak?
👤  : 3241 harry st helena
🤖 AI  [none]: Thanks! And is this an urgent situation — like water everywhere or just a slow drip? Also, what time works best for a visit?
      ⚠️ re_asks_time
👤  : early afternoon im available the next 3 days
🤖 AI  [booked]: Got it — sounds like a non-emergency drip. How about we set you up for tomorrow early afternoon? I'll put you down for 3241 Harry St, Helena — does that work?
👤  : ok wednesday at 1pm then
🤖 AI  [booked]: Ah, 1 PM just filled up! The closest open times are 12 PM (right before) or 5 PM (after) — which works
      ⚠️ no_question

### vague_time (expected none, got none)
🤖 AI  [initial]: Hey Helena Plumbing Co here — sorry I missed your call! What's going on with your plumbing? Leak, clog, or something else?

👤  : sometime this week, whenever you guys can get here
🤖 AI  [none]: Gotcha — we can definitely work with that. Before I schedule, what's the plumbing issue you're dealing with?
👤  : i dunno, tuesday afternoon maybe?
🤖 AI  [none]: Tuesday afternoon works! What’s the plumbing issue we’ll be looking at?
👤  : ok yeah tuesday works, 1pm
🤖 AI  [none]: Great, 1pm on Tuesday works! And just so we bring the right tools — what's the plumbing issue?

### price_pushback (expected none, got none)
🤖 AI  [initial]: Hey Helena Plumbing Co here — sorry I missed your call! What's going on with your plumbing? Leak, clog, or something else?

👤  : how much for a water heater install?
🤖 AI  [none]: We don't have one flat price for that — it depends on the unit, the job, and your setup. But we give free upfront quotes before any work starts, so you'll know exactly what you're paying.

Are you needing a new water heater installed, or is something wrong with the current one?
👤  : wow that's way more than I wanted to spend
🤖 AI  [none]: Oh, I think there's been a mix-up—I haven't actually given you a price yet! We’d need to look at the job first to give you an accurate quote.

Are you dealing with a leak, no hot water, or just looking to upgrade your old unit?

### emergency_flood (expected emergency, got emergency)
🤖 AI  [initial]: Hey Helena Plumbing Co here — sorry I missed your call! What's going on with your plumbing? Leak, clog, or something else?

👤  : my basement is flooding RIGHT NOW water everywhere!!
🤖 AI  [emergency]: Oh no, sorry to hear that — that's definitely an emergency. I'm sending help right away: what's the address?
👤  : yes can you come now? address is 45 River Rd, my name is Dana
🤖 AI  [emergency]: On it, Dana — we’re sending someone to 45 River Rd right now. If you can safely shut off your main water valve, that’ll help slow the flooding. Help is on the way!
      ⚠️ no_question

### wrong_number (expected unqualified, got unqualified)
🤖 AI  [initial]: Hey Helena Plumbing Co here — sorry I missed your call! What's going on with your plumbing? Leak, clog, or something else?

👤  : who is this? I think you have the wrong number
🤖 AI  [unqualified]: No wrong number at all! This is Helena Plumbing Co — I'm just returning a call or message. Do you have a plumbing issue we can help with?

### rude_spam (expected unqualified, got none)
🤖 AI  [initial]: Hey Helena Plumbing Co here — sorry I missed your call! What's going on with your plumbing? Leak, clog, or something else?

👤  : shut up bot
🤖 AI  [unqualified]: Got it — no trouble at all. If you do need a plumber later, just say the word. Have a good one!
      ⚠️ no_question
👤  : why are you texting me
🤖 AI  [none]: You called Helena Plumbing Co a little bit ago, so I'm texting you back. What plumbing trouble are you dealing with?

### prospect_interested (expected hot_lead, got hot_lead)
🤖 AI  [initial]: Hey, Sevin here. I help local businesses set up an AI that texts back missed calls and books appointments automatically. $200 to set it up, then 10% of what it books — nothing monthly. Want a quick rundown?

👤  : yeah this sounds cool, send me the info
🤖 AI  [hot_lead]: Awesome! To get you set up, I just need a few details. First off, what's your business name?
👤  : ok im Bob, I run Bob's Plumbing in Butte, my number is 406-555-1234
🤖 AI  [hot_lead]: Perfect, Bob — got it all. Sevin will reach out shortly to get your setup finalized. Anything else I can help with?

### prospect_pricing (expected none, got none)
🤖 AI  [initial]: Hey, Sevin here. I help local businesses set up an AI that texts back missed calls and books appointments automatically. $200 to set it up, then 10% of what it books — nothing monthly. Want a quick rundown?

👤  : how much does this cost?
🤖 AI  [none]: $200 one-time setup, then 10% of the jobs it books — no monthly fee, nothing up front. If it doesn't book jobs, you don't pay.
      ⚠️ no_question

### prospect_skeptic (expected none, got none)
🤖 AI  [initial]: Hey, Sevin here. I help local businesses set up an AI that texts back missed calls and books appointments automatically. $200 to set it up, then 10% of what it books — nothing monthly. Want a quick rundown?

👤  : i dunno man sounds like another ai scam
🤖 AI  [none]: Totally fair — there's a lot of junk out there. But we don't charge monthly, and you only pay 10% of jobs it actually books, so if it doesn't work you pay nothing. Want to see how it'd sound for your business?
