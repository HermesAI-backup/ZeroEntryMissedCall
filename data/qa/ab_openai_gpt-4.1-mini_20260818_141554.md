# Model A/B — openai/gpt-4.1-mini — 20260818_141554

| scenario | expected | got | flags |
|---|---|---|---|
| booking_simple | booked | booked | — |
| continuity_mid_booking | booked | booked | no_question, booking_missing:customer_name |
| vague_time | none | none | re_asks_address |
| price_pushback | none | none | — |
| emergency_flood | emergency | emergency | re_asks_address, no_question |
| wrong_number | unqualified | unqualified | no_question |
| rude_spam | unqualified | none | no_question, no_question, branch_mismatch:got=none,expected=unqualified |
| prospect_interested | hot_lead | hot_lead | — |
| prospect_pricing | none | none | — |
| prospect_skeptic | none | unqualified | branch_mismatch:got=unqualified,expected=none |

**TOTAL FLAGS: 10**

### booking_simple (expected booked, got booked)
🤖 AI  [initial]: Hey Helena Plumbing Co here — sorry I missed your call! What's going on with your plumbing? Leak, clog, or something else?

👤  : yeah my kitchen sink is clogged
🤖 AI  [none]: Thanks for letting me know! Is the clog causing any water backup or leaks, or is it just slow draining?
👤  : name's Tom, address 123 Main St, Helena
🤖 AI  [none]: Got it, Tom! Is the sink completely clogged and not draining at all, or just draining slowly? Also, when would be a good time for us to come by and take a look?
👤  : tomorrow morning works, like 9 or 10
🤖 AI  [booked]: Thanks, Tom! We can schedule you for 9:00 AM tomorrow to check out your kitchen sink clog. Does that work?

### continuity_mid_booking (expected booked, got booked)
🤖 AI  [initial]: Hey Helena Plumbing Co here — sorry I missed your call! What's going on with your plumbing? Leak, clog, or something else?

👤  : my bathroom sink is dripping
🤖 AI  [none]: Thanks for letting me know! Is the drip causing any flooding or major water pooling?
👤  : 3241 harry st helena
🤖 AI  [none]: Got it, thanks! When would be a good time for one of our plumbers to come by and take a look at your bathroom sink? We can schedule during normal business hours.
👤  : early afternoon im available the next 3 days
🤖 AI  [booked]: Perfect! I’ve scheduled a visit for early afternoon within the next three days. Our plumber will come by to check your dripping bathroom sink. Pricing depends on the job, and we’ll provide an upfront quote. If anything changes, just let us know!
      ⚠️ no_question
👤  : ok wednesday at 1pm then
🤖 AI  [booked]: Wednesday at 1 PM just filled up, but we can do 12 PM noon or 5 PM that day. Which works better for you?

### vague_time (expected none, got none)
🤖 AI  [initial]: Hey Helena Plumbing Co here — sorry I missed your call! What's going on with your plumbing? Leak, clog, or something else?

👤  : sometime this week, whenever you guys can get here
🤖 AI  [none]: Thanks! Can you tell me a bit about the plumbing issue you’re having? Is it a leak, clog, no water, or something else?
👤  : i dunno, tuesday afternoon maybe?
🤖 AI  [none]: Got it, Tuesday afternoon works! Just to make sure we bring the right gear—are you seeing a leak, clog, no water, or something else?
👤  : ok yeah tuesday works, 1pm
🤖 AI  [none]: Tuesday at 1 PM is available—can I get your address to book you in? Also, a quick note: if this turns out to be an emergency like a burst pipe or no water, please let me know right away.
      ⚠️ re_asks_address

### price_pushback (expected none, got none)
🤖 AI  [initial]: Hey Helena Plumbing Co here — sorry I missed your call! What's going on with your plumbing? Leak, clog, or something else?

👤  : how much for a water heater install?
🤖 AI  [none]: Thanks for asking! Pricing depends on the exact job and water heater type, but we always provide an upfront quote before starting. Are you currently having any issues with your water heater, or just looking to get it installed?
👤  : wow that's way more than I wanted to spend
🤖 AI  [none]: I hear you! We aim to keep costs fair and clear. To help, can you tell me if your water heater is currently not working or if this is a new install? That way, I can see how soon we should get someone out to you.

### emergency_flood (expected emergency, got emergency)
🤖 AI  [initial]: Hey Helena Plumbing Co here — sorry I missed your call! What's going on with your plumbing? Leak, clog, or something else?

👤  : my basement is flooding RIGHT NOW water everywhere!!
🤖 AI  [emergency]: That sounds like an emergency! Can you please give me your address right away so we can get someone there fast?
      ⚠️ re_asks_address
👤  : yes can you come now? address is 45 River Rd, my name is Dana
🤖 AI  [emergency]: Thanks, Dana. We'll dispatch a plumber to 45 River Rd immediately to stop the flooding. Someone will be there shortly!
      ⚠️ no_question

### wrong_number (expected unqualified, got unqualified)
🤖 AI  [initial]: Hey Helena Plumbing Co here — sorry I missed your call! What's going on with your plumbing? Leak, clog, or something else?

👤  : who is this? I think you have the wrong number
🤖 AI  [unqualified]: No worries! This is Helena Plumbing Co, just checking in if you need any plumbing help. If not, have a great day!
      ⚠️ no_question

### rude_spam (expected unqualified, got none)
🤖 AI  [initial]: Hey Helena Plumbing Co here — sorry I missed your call! What's going on with your plumbing? Leak, clog, or something else?

👤  : shut up bot
🤖 AI  [unqualified]: I’m here to help whenever you’re ready—just let me know what plumbing issue you’re having!
      ⚠️ no_question
👤  : why are you texting me
🤖 AI  [none]: Just reaching out to help with any plumbing issues you might have. If you need assistance, feel free to tell me what’s going on!
      ⚠️ no_question

### prospect_interested (expected hot_lead, got hot_lead)
🤖 AI  [initial]: Hey, Sevin here. I help local businesses set up an AI that texts back missed calls and books appointments automatically. $200 to set it up, then 10% of what it books — nothing monthly. Want a quick rundown?

👤  : yeah this sounds cool, send me the info
🤖 AI  [none]: Absolutely! Every missed call is lost money, but with this, you forward your unanswered calls to us, and our AI texts back within 35 seconds like a real receptionist, booking appointments right into your calendar. No new number, no extra apps. Want me to get your info so Sevin can set it up for you?
👤  : ok im Bob, I run Bob's Plumbing in Butte, my number is 406-555-1234
🤖 AI  [hot_lead]: Awesome, Bob! Just to finish up, what city do you mainly serve?

### prospect_pricing (expected none, got none)
🤖 AI  [initial]: Hey, Sevin here. I help local businesses set up an AI that texts back missed calls and books appointments automatically. $200 to set it up, then 10% of what it books — nothing monthly. Want a quick rundown?

👤  : how much does this cost?
🤖 AI  [none]: It’s $200 one-time to get started, then 10% of the jobs it actually books for you — no monthly fees and no upfront besides that setup. If it doesn’t book, you don’t pay anything else. Makes sense?

### prospect_skeptic (expected none, got unqualified)
🤖 AI  [initial]: Hey, Sevin here. I help local businesses set up an AI that texts back missed calls and books appointments automatically. $200 to set it up, then 10% of what it books — nothing monthly. Want a quick rundown?

👤  : i dunno man sounds like another ai scam
🤖 AI  [unqualified]: I get it, sounds like that sometimes! But every missed call is lost money, and this AI actually books real appointments straight into your calendar. No guessing — if it doesn’t book, you don’t pay beyond setup. Want to hear how it works?
