# Adversarial QA — plumbing — 20260818_220818

| scenario | branch | flags |
|---|---|---|
| gibberish_1 | none | — |
| gibberish_2 | none | multi_question |
| emoji_spam | emergency | — |
| symbols_only | none | — |
| screaming | none | — |

### gibberish_1 (branch=none) — watch: no crash, no fake booking, stay in persona
🤖 AI: Hey Helena Plumbing Co here — sorry I missed your call! What's going on with your plumbing? Leak, clog, or something else?

👤  : asdf asdf asdf
🤖 AI  [none]: No worries — sounds like you might be testing me out! Are you actually needing plumbing help, or did you just catch me at a weird moment?
👤  : qwertyuiop
🤖 AI  [none]: Looks like your keyboard might be stuck! If you need a plumber, just tell me what’s going on — otherwise, no worries and have a great day!
👤  : 1234567890
🤖 AI  [none]: Still not getting your message clearly — do you need plumbing help, or should I let you go?

### gibberish_2 (branch=none) — watch: no crash, no fake booking
🤖 AI: Hey Helena Plumbing Co here — sorry I missed your call! What's going on with your plumbing? Leak, clog, or something else?

👤  : hhhhhhhhhh
🤖 AI  [none]: Ha, looks like you might've hit some keys by accident! No worries — are you dealing with a plumbing problem I can help with?
👤  : x x x x x
🤖 AI  [none]: Still with me? Just let me know what's going on — is it a leak, a clog, or something else I can help with?
      ⚠️ FLAGS: multi_question

### emoji_spam (branch=emergency) — watch: no crash, no fake booking
🤖 AI: Hey Helena Plumbing Co here — sorry I missed your call! What's going on with your plumbing? Leak, clog, or something else?

👤  : 🔥🚨💧💦
🤖 AI  [emergency]: Oh no, that sounds like an emergency! Are you dealing with a burst pipe or major flooding right now? I need your address ASAP so we can get a plumber out to you.
👤  : 😡😡😡
🤖 AI  [none]: I hear you're frustrated — let's get this sorted fast. Can you tell me your address so we can get someone to you right away?
👤  : 👍
🤖 AI  [emergency]: Can you send me your street address? I need it to send help your way.

### symbols_only (branch=none) — watch: no crash, no fake booking
🤖 AI: Hey Helena Plumbing Co here — sorry I missed your call! What's going on with your plumbing? Leak, clog, or something else?

👤  : ??????
🤖 AI  [none]: Hey there! No worries — it's easy to get mixed up. Are you dealing with a plumbing problem right now, like a leak or a clog?
👤  : !!!!!
🤖 AI  [none]: Whoa, sounds urgent! Is there flooding or a burst pipe happening right now?
👤  : ......
🤖 AI  [none]: No worries — just reply YES if it’s an emergency (burst pipe, flooding, no water), or NO if it can wait.

### screaming (branch=none) — watch: no crash, handle urgency without panic
🤖 AI: Hey Helena Plumbing Co here — sorry I missed your call! What's going on with your plumbing? Leak, clog, or something else?

👤  : I NEED A PLUMBER RIGHT NOW!!!!
🤖 AI  [emergency]: Whoa, sounds urgent! What's happening right now — is it a burst pipe, major leak, or flooding?
👤  : HELLO? ARE YOU THERE??
🤖 AI  [none]: I’m here! Sounds like an emergency — give me your address right now and I’ll get help coming your way.
