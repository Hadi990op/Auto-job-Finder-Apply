#!/bin/bash
# Clear data and restart agent
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="${PYTHON_BIN:-python3}"

$PYTHON << 'PYEOF'
import sqlite3
import os
BASE_DIR = os.path.dirname(os.path.abspath("$SCRIPT_DIR/reset_and_run.sh".replace('"', '')))
# Fallback: use the directory of this script
import sys
script_dir = os.path.dirname(os.path.abspath(sys.argv[0])) if len(sys.argv) > 0 else os.getcwd()
db_path = os.path.join(script_dir, 'data', 'jobagent.db')
db = sqlite3.connect(db_path)
db.execute('DELETE FROM jobs')
db.execute('DELETE FROM applications')
db.execute('DELETE FROM activity_log')
db.execute('DELETE FROM seen_jobs')
db.execute("UPDATE agent_state SET total_discovered=0, total_evaluated=0, total_applied=0, total_notified=0, running=0, last_run='', next_run=''")
db.commit()
db.close()
print('Data cleared')
PYEOF

# Restart app
systemctl restart job-agent 2>/dev/null || echo "job-agent service not found (may not be installed)"
sleep 3
systemctl is-active job-agent 2>/dev/null || echo "Service not running"

# Trigger agent run
curl -s http://127.0.0.1:9300/api/run
echo ""
echo "Agent started"
