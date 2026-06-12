#!/bin/bash
echo "Starting Recoup Enterprise Platform..."

# Ensure dependencies are installed
echo "Checking dependencies..."
.venv/bin/pip install -r requirements.txt -q

# Start the FastAPI backend
echo "Starting backend API (uvicorn)..."
.venv/bin/uvicorn recoup_agent.api:app --host 127.0.0.1 --port 8001 --reload &
BACKEND_PID=$!

# Start the React frontend
echo "Starting frontend UI (Vite)..."
cd web && npm run dev &
FRONTEND_PID=$!

echo "--------------------------------------------------------"
echo "Recoup is running!"
echo "- Frontend: http://localhost:5173"
echo "- Backend API: http://localhost:8001"
echo "Press Ctrl+C to stop all services."
echo "--------------------------------------------------------"

# Give services a second to boot, then open the browser
sleep 2
open http://localhost:5173

# Wait for Ctrl+C
trap "kill $BACKEND_PID $FRONTEND_PID" SIGINT
wait
