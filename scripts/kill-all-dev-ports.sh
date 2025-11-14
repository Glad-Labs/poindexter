#!/bin/bash

# Force Kill All Development Processes

echo "Finding and killing all development processes..."

# List all PIDs
PIDS=$(netstat -ano | grep LISTENING | grep -E ":(1337|3000|3001|8000)" | awk '{print $NF}' | sort -u)

echo "Found processes: $PIDS"
echo ""

for PID in $PIDS; do
    if [ -n "$PID" ] && [ "$PID" != "0" ]; then
        echo "Killing PID $PID..."
        # Try multiple methods
        kill -9 "$PID" 2>/dev/null || true
        sleep 1
    fi
done

echo ""
echo "Waiting 3 seconds for cleanup..."
sleep 3

echo "Checking remaining processes..."
netstat -ano | grep LISTENING | grep -E ":(1337|3000|3001|8000)" || echo "✓ All ports are now free!"

echo ""
echo "Done!"
