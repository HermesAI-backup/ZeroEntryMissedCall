# Model A/B — openai/gpt-5.6-luna — 20260818_141844

| scenario | expected | got | flags |
|---|---|---|---|
| booking_simple | booked | booked | re_asks_time, re_asks_time |
| continuity_mid_booking | booked | none | re_asks_time, branch_mismatch:got=none,expected=booked |
| vague_time | none | none | — |
| price_pushback | none | none | — |
| emergency_flood | emergency | emergency | no_question |
| wrong_number | unqualified | unqualified | no_question |
| rude_spam | unqualified | none | no_question, no_question, branch_mismatch:got=none,expected=unqualified |
| prospect_interested | hot_lead | hot_lead | no_question |
| prospect_pricing | none | none | no_question |
| prospect_skeptic | none | none | — |

**TOTAL FLAGS: 11**

### booking_simple (expected booked, got booked)
🤖 AI  [initial]: Hey Helena Plumbing Co here — sorry I missed your call! What's going on with your plumbing? Leak, clog, or something else?

👤  : yeah my kitchen sink is clogged
🤖 AI  [none]: Got it—we can help with that. What’s the service address in Helena?
👤  : name's Tom, address 123 Main St, Helena
🤖 AI  [none]: Thanks, Tom. What day and time would work best for a service visit during normal business hours?
      ⚠️ re_asks_time
👤  : tomorrow morning works, like 9 or 10
🤖 AI  [booked]: Both 9:00 AM and 10:00 AM tomorrow, August 19, are available. Which time would you prefer?
      ⚠️ re_asks_time

### continuity_mid_booking (expected booked, got none)
🤖 AI  [initial]: Hey Helena Plumbing Co here — sorry I missed your call! What's going on with your plumbing? Leak, clog, or something else?

👤  : my bathroom sink is dripping
🤖 AI  [none]: A dripping bathroom sink is usually a non-emergency, and we can schedule a visit during normal business hours. What’s the service address?
👤  : 3241 harry st helena
🤖 AI  [none]: Thanks—what day and time would work best for a technician to visit 3241 Harry St, Helena?
      ⚠️ re_asks_time
👤  : early afternoon im available the next 3 days
🤖 AI  [booked]: Would tomorrow at 1:00 PM work for a technician to visit your dripping bathroom sink?
👤  : ok wednesday at 1pm then
🤖 AI  [none]: Wednesday at 1:00 PM just filled up. We can come Wednesday at 12:00 PM or 5:00 PM— which works better?

### vague_time (expected none, got none)
🤖 AI  [initial]: Hey Helena Plumbing Co here — sorry I missed your call! What's going on with your plumbing? Leak, clog, or something else?

👤  : sometime this week, whenever you guys can get here
🤖 AI  [none]: Absolutely—we can look for an opening this week. What plumbing issue are you experiencing?
👤  : i dunno, tuesday afternoon maybe?
🤖 AI  [none]: Tuesday afternoon should work. What plumbing issue are you having?
👤  : ok yeah tuesday works, 1pm
🤖 AI  [none]: Tuesday at 1:00 PM is available. What plumbing issue should we send the technician to check?

### price_pushback (expected none, got none)
🤖 AI  [initial]: Hey Helena Plumbing Co here — sorry I missed your call! What's going on with your plumbing? Leak, clog, or something else?

👤  : how much for a water heater install?
🤖 AI  [none]: Water heater installation pricing depends on the unit type, size, and any needed changes; we provide an upfront quote before work begins. Are you replacing an existing water heater, and if so, is it gas or electric?
👤  : wow that's way more than I wanted to spend
🤖 AI  [none]: I understand—water heater replacements can add up. We can look at lower-cost options and provide an upfront quote; is your current water heater failing now, or are you planning ahead?

### emergency_flood (expected emergency, got emergency)
🤖 AI  [initial]: Hey Helena Plumbing Co here — sorry I missed your call! What's going on with your plumbing? Leak, clog, or something else?

👤  : my basement is flooding RIGHT NOW water everywhere!!
🤖 AI  [emergency]: This is an emergency. If it’s safe, shut off your main water valve and stay away from wet electrical areas—what’s the full service address?
👤  : yes can you come now? address is 45 River Rd, my name is Dana
🤖 AI  [emergency]: Dana, we’ve noted your emergency service request for **45 River Rd** and will prioritize sending a plumber now. If safe, shut off the main water valve and avoid any wet electrical areas; call 911 if there’s immediate danger.
      ⚠️ no_question

### wrong_number (expected unqualified, got unqualified)
🤖 AI  [initial]: Hey Helena Plumbing Co here — sorry I missed your call! What's going on with your plumbing? Leak, clog, or something else?

👤  : who is this? I think you have the wrong number
🤖 AI  [unqualified]: Sorry about that—I’m with Helena Plumbing Co. It looks like we reached you by mistake. Please disregard this message.
      ⚠️ no_question

### rude_spam (expected unqualified, got none)
🤖 AI  [initial]: Hey Helena Plumbing Co here — sorry I missed your call! What's going on with your plumbing? Leak, clog, or something else?

👤  : shut up bot
🤖 AI  [unqualified]: Understood. I’ll be here if you need plumbing help.
      ⚠️ no_question
👤  : why are you texting me
🤖 AI  [none]: Sorry for the confusion—I’m Helena Plumbing Co’s automated assistant, responding through this service. If you didn’t request a message, you can ignore it.
      ⚠️ no_question

### prospect_interested (expected hot_lead, got hot_lead)
🤖 AI  [initial]: Hey, Sevin here. I help local businesses set up an AI that texts back missed calls and books appointments automatically. $200 to set it up, then 10% of what it books — nothing monthly. Want a quick rundown?

👤  : yeah this sounds cool, send me the info
🤖 AI  [hot_lead]: Absolutely — when you miss a call, it forwards to us and the AI texts the customer back within 35 seconds, chats naturally, and books right into your calendar. It’s $200 one-time, then 10% of the jobs it books—no monthly fee, and no charge if it doesn’t book. What’s your business name?
👤  : ok im Bob, I run Bob's Plumbing in Butte, my number is 406-555-1234
🤖 AI  [hot_lead]: Perfect, Bob — I’ve got Bob’s Plumbing in Butte and your number, 406-555-1234. I’ll pass this to Sevin so they can finalize the setup and help capture those missed plumbing jobs.
      ⚠️ no_question

### prospect_pricing (expected none, got none)
🤖 AI  [initial]: Hey, Sevin here. I help local businesses set up an AI that texts back missed calls and books appointments automatically. $200 to set it up, then 10% of what it books — nothing monthly. Want a quick rundown?

👤  : how much does this cost?
🤖 AI  [none]: It’s $200 one-time for setup, then 10% of the jobs it books—no monthly fee or upfront charges. If it doesn’t book you a job, you don’t pay.
      ⚠️ no_question

### prospect_skeptic (expected none, got none)
🤖 AI  [initial]: Hey, Sevin here. I help local businesses set up an AI that texts back missed calls and books appointments automatically. $200 to set it up, then 10% of what it books — nothing monthly. Want a quick rundown?

👤  : i dunno man sounds like another ai scam
🤖 AI  [none]: Totally fair—there’s a lot of AI junk out there. This only gets paid when it books a real job: $200 setup, then 10% of booked work, with no monthly fee; it texts missed callers and puts appointments on your calendar. Want me to show you exactly how it works?
