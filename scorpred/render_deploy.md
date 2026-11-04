# Render deploy script for ScorPred
#
# This script summarizes the steps to deploy ScorPred on Render.com with persistent disk and environment variables.
#
# 1. Push your code to GitHub (private or public repo).
# 2. In Render, click "New Web Service" and connect your repo.
# 3. Confirm the start command is:
#    gunicorn app:app --workers 2 --threads 2 --timeout 60 --bind 0.0.0.0:$PORT
# 4. Add a Persistent Disk (e.g. mount at /persistent, 1GB+).
# 5. Set environment variables:
#    - SECRET_KEY: (any long random string)
#    - SCORPRED_DATA_ROOT: /persistent
#    - API_FOOTBALL_KEY, NBA_API_KEY, ANTHROPIC_API_KEY as needed
# 6. Deploy. Health check passes when / returns HTTP 200.
#
# See README.md for details.
