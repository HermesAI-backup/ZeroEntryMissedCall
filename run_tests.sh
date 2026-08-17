#!/bin/bash
# Canonical verification for missed-call-ai.
# Usage: bash run_tests.sh
# Compiles every module and runs every committed plain-python regression test.
set -u
cd "$(dirname "$0")" || exit 9
P=0; F=0
ok(){ echo "  PASS: $1"; P=$((P+1)); }
bad(){ echo "  FAIL: $1"; F=$((F+1)); }

echo '=== compile all modules ==='
for m in app.py conversation.py branching.py llm_client.py scheduler.py database.py \
         automation.py telnyx_client.py health_monitor.py backup_data.py config.py; do
  python -m py_compile "$m" 2>/dev/null && ok "$m" || bad "$m"
done

echo '=== committed regression tests ==='
for t in test_*.py; do
  case "$t" in
    test_missed_call_plumbing.py|test_watchdog_webhooks.py) continue ;;  # live-API tests, run manually
  esac
  python "$t" >/dev/null 2>&1 && ok "$t" || bad "$t"
done

echo '=== prompt YAMLs parse + engine budgets ==='
python - <<'PYEOF'
import sys, yaml
from pathlib import Path
from conversation import ConversationEngine
for p in sorted(Path("prompts").glob("*.yaml")):
    d = yaml.safe_load(p.read_text(encoding="utf-8"))
    assert d.get("max_ai_responses", 0) == 10, p
    assert ConversationEngine(business_type=p.stem).max_responses() == 10, p
print("    prompts ok")
PYEOF
[ $? -eq 0 ] && ok 'prompt YAMLs' || bad 'prompt YAMLs'

echo "=== VERDICT: $P passed, $F failed ==="
[ "$F" -eq 0 ]
