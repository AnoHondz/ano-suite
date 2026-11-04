#!/bin/bash
cd "$(dirname "$0")/frontend"
echo "Starting ScorPred Frontend..."
echo ""
echo "Checking for npm..."
which npm || { echo "❌ npm not found. Installing node..."; exit 1; }
echo "✅ npm found!"
echo ""
echo "Starting dev server..."
npm run dev
