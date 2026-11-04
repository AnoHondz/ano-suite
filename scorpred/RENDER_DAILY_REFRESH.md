# Render Scheduled Job: ScorPred Daily Refresh

This script will run the daily_refresh.py pipeline every night at 2am UTC.
It ensures all played games are graded and real-world stats are shown in the UI.

## How to set up on Render

1. Go to your Render dashboard.
2. Open your ScorPred service.
3. Go to the 'Jobs' tab (or 'Scheduled Jobs').
4. Click 'New Job'.
5. Set the command to:

    python daily_refresh.py

6. Set the schedule to:

    0 2 * * *

   (This means every day at 2:00am UTC)

7. Save the job. Render will now run the refresh automatically every night.

---

## Manual refresh

You can also run it manually anytime:

    python daily_refresh.py

After running, reload your app to see updated results.
