"""
seed_tracking_data.py — Generate realistic completed prediction tracking data.

This script seeds the tracking database with historical predictions to make
Performance and Strategy Lab dashboards look alive with realistic past data.

Usage:
    python seed_tracking_data.py          # Seed sample data
    python seed_tracking_data.py --reset  # Clear seeded data
"""

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any
import random

import model_tracker as mt
from runtime_paths import prediction_tracking_path


# Real EPL teams
EPL_TEAMS = [
    ("Manchester United", "Liverpool", 1, 2, 33, 34),
    ("Manchester City", "Chelsea", 3, 4, 42, 5),
    ("Arsenal", "Tottenham", 5, 6, 1, 14),
    ("Brighton", "Newcastle", 7, 8, 11, 4),
    ("Aston Villa", "Fulham", 9, 10, 7, 19),
    ("Brentford", "West Ham", 11, 12, 51, 21),
    ("Everton", "Leicester", 13, 14, 13, 2),
    ("Nottingham Forest", "Bournemouth", 15, 16, 35, 127),
    ("Ipswich Town", "Southampton", 17, 18, 40, 41),
    ("Crystal Palace", "Wolverhampton", 19, 20, 27, 39),
]

# Real NBA teams
NBA_TEAMS = [
    ("Boston Celtics", "Miami Heat", "BOS", "MIA"),
    ("Los Angeles Lakers", "Denver Nuggets", "LAL", "DEN"),
    ("Golden State Warriors", "Sacramento Kings", "GSW", "SAC"),
    ("Phoenix Suns", "Dallas Mavericks", "PHX", "DAL"),
    ("Milwaukee Bucks", "New York Knicks", "MIL", "NYK"),
    ("Los Angeles Clippers", "Houston Rockets", "LAC", "HOU"),
    ("Chicago Bulls", "Atlanta Hawks", "CHI", "ATL"),
    ("Toronto Raptors", "Philadelphia 76ers", "TOR", "PHI"),
]


def _generate_realistic_soccer_predictions(days_back: int = 90, count: int = 25) -> list[dict]:
    """
    Generate realistic completed soccer predictions.
    
    Args:
        days_back: How many days back to generate predictions for
        count: Number of predictions to generate
    
    Returns:
        List of completed prediction records
    """
    predictions = []
    now_utc = datetime.now(timezone.utc)
    
    confidence_weights = {
        "High": 0.40,    # 40% High confidence
        "Medium": 0.35,  # 35% Medium
        "Low": 0.25,     # 25% Low (higher risk)
    }
    
    # Expected accuracy mix: ~65% win rate (realistic)
    win_rate = 0.65
    
    for i in range(count):
        # Randomize days back
        days_offset = random.randint(7, days_back)
        game_date = now_utc - timedelta(days=days_offset)
        created_date = game_date - timedelta(hours=random.randint(6, 48))
        
        team_a_name, team_b_name, team_a_id, team_b_id, league_id, season = random.choice(EPL_TEAMS)
        
        # Determine if this will be a win or loss
        is_win = random.random() < win_rate
        
        # Assign confidence levels (high confidence has higher win rate)
        confidence_roll = random.random()
        cumulative = 0
        confidence = "Low"
        for conf, weight in confidence_weights.items():
            cumulative += weight
            if confidence_roll < cumulative:
                confidence = conf
                break
        
        # Adjust win probability based on confidence
        if confidence == "High":
            win_prob = random.uniform(0.68, 0.85)
        elif confidence == "Medium":
            win_prob = random.uniform(0.50, 0.68)
        else:
            win_prob = random.uniform(0.35, 0.50)
        
        # Randomly choose predicted winner or draw
        pred_roll = random.random()
        if pred_roll < win_prob:
            predicted_winner = "A"
            team_a_prob = round(win_prob, 2)
        elif pred_roll < win_prob + 0.15:
            predicted_winner = "B"
            team_a_prob = round(1 - win_prob * 0.8, 2)
        else:
            predicted_winner = "draw"
            team_a_prob = round(win_prob * 0.6, 2)
        
        team_b_prob = round((1 - team_a_prob) / 2, 2)
        draw_prob = round(1 - team_a_prob - team_b_prob, 2)
        
        # Generate actual result based on is_win flag
        actual_outcomes = ["A", "B", "draw"]
        
        if is_win:
            # Actual result matches prediction
            actual_result = predicted_winner
        else:
            # Actual result doesn't match (create realistic loss)
            possible = [o for o in actual_outcomes if o != predicted_winner]
            actual_result = random.choice(possible)
        
        # Generate realistic final score
        if predicted_winner == "A" and actual_result == "A":
            final_score = {"a": random.randint(1, 3), "b": random.randint(0, 1)}
        elif predicted_winner == "B" and actual_result == "B":
            final_score = {"a": random.randint(0, 1), "b": random.randint(1, 3)}
        elif actual_result == "draw":
            final_score = {"a": random.randint(0, 2), "b": random.randint(0, 2)}
        else:
            # Loss case
            final_score = {"a": random.randint(0, 2), "b": random.randint(0, 2)}
            if actual_result == "B":
                final_score["b"] = max(final_score["a"] + 1, final_score["b"])
            else:
                final_score["a"] = max(final_score["b"] + 1, final_score["a"])
        
        pred_id = f"seed_{i:03d}_{random.randint(1000, 9999)}"
        
        prediction = {
            "id": pred_id,
            "sport": "soccer",
            "date": game_date.strftime("%Y-%m-%d"),
            "game_date": game_date.strftime("%Y-%m-%d"),
            "team_a": team_a_name,
            "team_b": team_b_name,
            "team_a_id": str(team_a_id),
            "team_b_id": str(team_b_id),
            "predicted_winner": predicted_winner,
            "actual_result": actual_result,
            "prob_a": team_a_prob,
            "prob_b": team_b_prob,
            "prob_draw": draw_prob,
            "confidence": confidence,
            "status": "completed",
            "is_correct": predicted_winner == actual_result,
            "created_at": created_date.isoformat().replace("+00:00", "Z"),
            "updated_at": (created_date + timedelta(days=1)).isoformat().replace("+00:00", "Z"),
            "league_id": league_id,
            "season": season,
            "final_score": final_score,
            "is_seeded": True,
        }
        predictions.append(prediction)
    
    return predictions


def _generate_realistic_nba_predictions(days_back: int = 90, count: int = 15) -> list[dict]:
    """
    Generate realistic completed NBA predictions.
    
    Args:
        days_back: How many days back to generate predictions for
        count: Number of predictions to generate
    
    Returns:
        List of completed prediction records
    """
    predictions = []
    now_utc = datetime.now(timezone.utc)
    
    confidence_weights = {"High": 0.40, "Medium": 0.35, "Low": 0.25}
    win_rate = 0.63  # NBA slightly higher variance
    
    for i in range(count):
        days_offset = random.randint(7, days_back)
        game_date = now_utc - timedelta(days=days_offset)
        created_date = game_date - timedelta(hours=random.randint(4, 24))
        
        team_a_name, team_b_name, team_a_abbr, team_b_abbr = random.choice(NBA_TEAMS)
        
        is_win = random.random() < win_rate
        
        # Confidence distribution
        confidence_roll = random.random()
        cumulative = 0
        confidence = "Low"
        for conf, weight in confidence_weights.items():
            cumulative += weight
            if confidence_roll < cumulative:
                confidence = conf
                break
        
        # Win probability based on confidence
        if confidence == "High":
            win_prob = random.uniform(0.60, 0.72)
        elif confidence == "Medium":
            win_prob = random.uniform(0.48, 0.60)
        else:
            win_prob = random.uniform(0.35, 0.48)
        
        # NBA rarely has draws
        if random.random() < 0.95:
            if random.random() < win_prob:
                predicted_winner = "A"
                team_a_prob = round(win_prob + 0.05, 2)
            else:
                predicted_winner = "B"
                team_a_prob = round(1 - win_prob - 0.05, 2)
            team_b_prob = round(1 - team_a_prob, 2)
            draw_prob = 0.0
        else:
            # Very rare draw (OT confusion in real data)
            predicted_winner = "A"
            team_a_prob = 0.33
            team_b_prob = 0.33
            draw_prob = 0.34
        
        # Generate actual result
        if is_win:
            actual_result = predicted_winner
        else:
            actual_result = "B" if predicted_winner == "A" else "A"
        
        # Generate realistic NBA score
        if actual_result == "A":
            if is_win:
                final_score = {"a": random.randint(105, 125), "b": random.randint(95, 110)}
            else:
                final_score = {"a": random.randint(105, 125), "b": random.randint(95, 110)}
        else:
            if is_win:
                final_score = {"a": random.randint(95, 110), "b": random.randint(105, 125)}
            else:
                final_score = {"a": random.randint(95, 110), "b": random.randint(105, 125)}
        
        pred_id = f"seed_nba_{i:03d}_{random.randint(1000, 9999)}"
        
        prediction = {
            "id": pred_id,
            "sport": "nba",
            "date": game_date.strftime("%Y-%m-%d"),
            "game_date": game_date.strftime("%Y-%m-%d"),
            "team_a": team_a_name,
            "team_b": team_b_name,
            "team_a_id": team_a_abbr,
            "team_b_id": team_b_abbr,
            "predicted_winner": predicted_winner,
            "actual_result": actual_result,
            "prob_a": team_a_prob,
            "prob_b": team_b_prob,
            "prob_draw": draw_prob,
            "confidence": confidence,
            "status": "completed",
            "is_correct": predicted_winner == actual_result,
            "created_at": created_date.isoformat().replace("+00:00", "Z"),
            "updated_at": (created_date + timedelta(days=1)).isoformat().replace("+00:00", "Z"),
            "league_id": 12408,  # NBA league ID
            "season": 2026,
            "final_score": final_score,
            "is_seeded": True,
        }
        predictions.append(prediction)
    
    return predictions


def seed_tracking_data() -> None:
    """
    Seed tracking database with realistic completed predictions.
    """
    print("🌱 Seeding tracking data...")
    
    tracking_file = str(prediction_tracking_path())
    
    # Load existing predictions
    mt._ensure_tracking_file()
    existing = mt._load_predictions()
    
    # Filter out seeded data to avoid duplicates
    non_seeded = [p for p in existing if not p.get("id", "").startswith("seed_")]
    
    # Generate new seed data
    soccer_preds = _generate_realistic_soccer_predictions(days_back=90, count=25)
    nba_preds = _generate_realistic_nba_predictions(days_back=90, count=15)
    
    all_seeded = soccer_preds + nba_preds
    
    # Combine with existing non-seeded predictions
    combined = non_seeded + all_seeded
    
    # Save
    folder = os.path.dirname(tracking_file)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder, exist_ok=True)
    
    with open(tracking_file, "w", encoding="utf-8") as f:
        json.dump({"predictions": combined}, f, indent=2)
    
    # Calculate summary
    soccer = [p for p in all_seeded if p["sport"] == "soccer"]
    nba = [p for p in all_seeded if p["sport"] == "nba"]
    
    soccer_wins = len([p for p in soccer if p["is_correct"]])
    nba_wins = len([p for p in nba if p["is_correct"]])
    
    print(f"\n✅ Seeded tracking data successfully!")
    print(f"\n📊 Seeded Summary:")
    print(f"  Soccer predictions: {len(soccer)} ({soccer_wins} wins, {len(soccer) - soccer_wins} losses)")
    print(f"  NBA predictions: {len(nba)} ({nba_wins} wins, {len(nba) - nba_wins} losses)")
    print(f"  Overall accuracy: {((soccer_wins + nba_wins) / len(all_seeded) * 100):.1f}%")
    print(f"\n📁 Tracking file: {tracking_file}")
    print(f"   Total predictions now: {len(combined)}")


def reset_tracking_data(keep_non_seeded: bool = True) -> None:
    """
    Clear seeded tracking data.
    
    Args:
        keep_non_seeded: If True, keep non-seeded predictions; if False, clear all
    """
    tracking_file = str(prediction_tracking_path())
    
    if keep_non_seeded:
        mt._ensure_tracking_file()
        existing = mt._load_predictions()
        non_seeded = [p for p in existing if not p.get("id", "").startswith("seed_")]
        
        with open(tracking_file, "w", encoding="utf-8") as f:
            json.dump({"predictions": non_seeded}, f, indent=2)
        
        print(f"✅ Cleared seeded data. Kept {len(non_seeded)} non-seeded predictions.")
    else:
        folder = os.path.dirname(tracking_file)
        if folder and not os.path.isdir(folder):
            os.makedirs(folder, exist_ok=True)
        
        with open(tracking_file, "w", encoding="utf-8") as f:
            json.dump({"predictions": []}, f, indent=2)
        
        print("✅ Cleared all tracking data.")


def main():
    parser = argparse.ArgumentParser(
        description="Seed or reset tracking data for ScorPred dashboards"
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Clear seeded predictions (keep non-seeded data)",
    )
    parser.add_argument(
        "--reset-all",
        action="store_true",
        help="Clear ALL tracking data (including non-seeded)",
    )
    
    args = parser.parse_args()
    
    if args.reset:
        reset_tracking_data(keep_non_seeded=True)
    elif args.reset_all:
        reset_tracking_data(keep_non_seeded=False)
    else:
        seed_tracking_data()


if __name__ == "__main__":
    main()
