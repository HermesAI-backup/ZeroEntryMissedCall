"""Head-to-head: booking-extractor reliability across candidate models.

Uses the EXACT disaster input from the live session ("2pm this wed" when 2pm
was TAKEN on the calendar). Measures, per model:
  - first-pass shape: does chat_structured return the 4 required keys?
  - extracted values: correct date/time?
Runs 3 reps each to expose non-determinism. Raw HTTP to the Hermes proxy
(bypasses settings lru_cache). No I/O, no SMS.
"""
import asyncio, json, os, sys, time, urllib.request

PROXY = "http://127.0.0.1:8645/v1"

HISTORY = [
    {"role": "system", "content": "[Missed call context]"},
    {"role": "assistant", "content": "Hey Helena Plumbing Co here — sorry I missed your call!"},
    {"role": "user", "content": "Hello yes I have an issue with my sink its clogged. Are you available at 2pm this wed?"},
]

SCHEMA = {
    "type": "object",
    "properties": {
        "customer_name": {"type": "string", "description": "customer full name or empty"},
        "address": {"type": "string", "description": "service address or empty"},
        "appt_date": {"type": "string", "description": "YYYY-MM-DD or empty"},
        "appt_time": {"type": "string", "description": "HH:MM or empty"},
    },
    "required": ["customer_name", "address", "appt_date", "appt_time"],
}

SYSTEM = (
    "You are a booking-detail extractor. Read the conversation below and extract the "
    "customer's booking information. Return ONLY valid JSON with the exact keys: "
    'customer_name, address, appt_date ("YYYY-MM-DD"), appt_time ("HH:MM"). '
    'Use empty strings for fields not provided. Today is 2026-08-18 (Tuesday).'
)

MODELS = [
    "deepseek/deepseek-v4-flash",      # current primary
    "openai/gpt-4.1-mini",             # current fallback tier
    "openai/gpt-5.6-luna",             # mid GPT
    "x-ai/grok-4.6",                   # grok
    "anthropic/claude-opus-4.7-fast",  # top-tier claude
]

REQUIRED = set(SCHEMA["required"])

def raw_chat(model: str) -> dict:
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM}, *HISTORY],
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }).encode()
    req = urllib.request.Request(
        f"{PROXY}/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer unused",
            # The Hermes proxy 403s the default Python-urllib UA (known quirk)
            "User-Agent": "model-benchmark/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read().decode())
    content = data["choices"][0]["message"]["content"]
    # strip code fences if the model wrapped the JSON
    content = content.strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[1].rsplit("```", 1)[0]
    return json.loads(content)

def probe(model: str, reps: int = 3) -> dict:
    first_pass_ok = 0
    correct = 0
    times = []
    samples = []
    for i in range(reps):
        t0 = time.time()
        try:
            result = raw_chat(model)
        except Exception as e:
            samples.append({"error": str(e)[:100]})
            times.append(None)
            continue
        dt = time.time() - t0
        times.append(round(dt, 1))
        if isinstance(result, dict) and all(k in result for k in REQUIRED):
            first_pass_ok += 1
            d, tm = result.get("appt_date", ""), result.get("appt_time", "")
            if d == "2026-08-19" and tm == "14:00":
                correct += 1
                samples.append({"date": d, "time": tm})
            else:
                samples.append({"date": d, "time": tm, "keys_ok": True})
        else:
            samples.append({"shape": list(result.keys()) if isinstance(result, dict) else type(result).__name__})
    return {
        "model": model,
        "first_pass_shape_ok": f"{first_pass_ok}/{reps}",
        "correct_value": f"{correct}/{reps}",
        "latency_s": times,
        "samples": samples,
    }

def main():
    results = []
    for m in MODELS:
        print(f"probing {m} ...", flush=True)
        try:
            results.append(probe(m))
        except Exception as e:
            results.append({"model": m, "error": str(e)[:120]})
    print("\n" + "=" * 78)
    print(f"{'MODEL':<34} {'shape-ok':<10} {'correct':<9} latency")
    print("-" * 78)
    for r in results:
        if "error" in r:
            print(f"{r['model']:<34} ERROR: {r['error']}")
        else:
            print(f"{r['model']:<34} {r['first_pass_shape_ok']:<10} {r['correct_value']:<9} {r['latency_s']}")
    print("=" * 78)
    for r in results:
        if "error" not in r:
            print(f"\n{r['model']} samples: {json.dumps(r['samples'], ensure_ascii=False)}")

if __name__ == "__main__":
    main()
