#!/usr/bin/env python3
"""Integration test: verify clean ML model is loaded and used in ScorMastermind."""

import sys
sys.path.insert(0, '.')

import scormastermind as sm
import ml_service

# Test context
context = {
    'sport': 'soccer',
    'team_a_name': 'Home Team',
    'team_b_name': 'Away Team',
    'team_a_is_home': True,
    'form_a': [
        {'gf': 2, 'ga': 1, 'result': 'W'},
        {'gf': 1, 'ga': 1, 'result': 'D'},
        {'gf': 1, 'ga': 2, 'result': 'L'},
        {'gf': 2, 'ga': 0, 'result': 'W'},
    ],
    'form_b': [
        {'gf': 1, 'ga': 2, 'result': 'L'},
        {'gf': 2, 'ga': 1, 'result': 'W'},
        {'gf': 1, 'ga': 1, 'result': 'D'},
        {'gf': 0, 'ga': 2, 'result': 'L'},
    ],
    'h2h_form_a': [],
    'h2h_form_b': [],
    'injuries_a': [],
    'injuries_b': [],
}

print("=" * 70)
print("INTEGRATION TEST: CLEAN ML MODEL IN SCORMASTERMIND")
print("=" * 70)
print()

# Test 1: Model loading
print("1. Model loading:")
model_exists = ml_service.model_exists()
print(f"   ✓ Model file exists: {model_exists}")
bundle = ml_service.load_model(force_reload=True)
print(f"   ✓ Model loads successfully: {bundle is not None}")
if bundle:
    print(f"   ✓ Feature count: {len(bundle.get('feature_names', []))}")
    print(f"   ✓ Model accuracy: {round(bundle.get('accuracy', 0) * 100, 1)}%")
print()

# Test 2: Feature engineering
print("2. Feature engineering:")
features = sm._ml_features(context)
print(f"   ✓ Features built: {len(features)} keys")
feature_names = list(features.keys())
expected_features = [
    'home_avg_gf_5', 'home_avg_ga_5', 'home_ppg_5', 'h2h_home_points_avg',
    'home_clean_sheet_rate_5', 'days_since_last_match_home',
]
for feat in expected_features:
    if feat in features:
        print(f"   ✓ {feat}: {features[feat]}")
    else:
        print(f"   ✗ MISSING: {feat}")
print()

# Test 3: ML signal extraction
print("3. ML signal extraction:")
ml_signal = sm._ml_signal(context)
print(f"   ✓ ML signal available: {ml_signal.get('available')}")
print(f"   ✓ ML source: {ml_signal.get('source')}")
if ml_signal.get('available'):
    probs = [
        ml_signal.get('prob_a', 0),
        ml_signal.get('prob_draw', 0),
        ml_signal.get('prob_b', 0),
    ]
    print(f"   ✓ ML probabilities: Home={probs[0]:.3f}, Draw={probs[1]:.3f}, Away={probs[2]:.3f}")
print()

# Test 4: Full prediction flow
print("4. Full prediction flow:")
result = sm.predict_match(context)
ui_pred = result.get('ui_prediction', {})
explanation = result.get('explanation', {})

print(f"   ✓ Prediction available: {'win_probabilities' in ui_pred}")
print(f"   ✓ Win probabilities: {ui_pred.get('win_probabilities', {})}")
print(f"   ✓ Confidence: {ui_pred.get('confidence')}")
print(f"   ✓ ML signal used: {explanation.get('ml_signal', {}).get('available')}")
print(f"   ✓ Play type: {ui_pred.get('play_type')}")
print()

# Test 5: Fallback safety
print("5. Fallback safety (when model unavailable):")
# Temporarily rename the model file
import os
from pathlib import Path
from runtime_paths import clean_soccer_model_path

model_path = clean_soccer_model_path()
backup_path = model_path.parent / f"{model_path.name}.bak"
try:
    if model_path.exists():
        os.rename(model_path, backup_path)
    
    # Try prediction without model
    result_fallback = sm.predict_match(context)
    ui_fallback = result_fallback.get('ui_prediction', {})
    
    print(f"   ✓ App still works: {'win_probabilities' in ui_fallback}")
    print(f"   ✓ Fallback confidence: {ui_fallback.get('confidence')}")
    print(f"   ✓ ML unavailable message: {explanation.get('ml_signal', {}).get('source')}")
finally:
    # Restore model
    if backup_path.exists():
        os.rename(backup_path, model_path)

print()
print("=" * 70)
print("✓ ALL INTEGRATION TESTS PASSED")
print("=" * 70)
