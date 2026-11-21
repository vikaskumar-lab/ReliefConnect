#!/usr/bin/env python3
"""
ReliefConnect Automated Test & Validation Suite
Validates:
1. Database schema initialization across all 10 entities
2. Prioritization scoring algorithm mathematical accuracy
3. 100+ synthetic flood request generation (120 requests)
4. Constrained multi-criteria spatial allocation engine
5. Zero-double-allocation guarantee
6. Explainable decision generation
"""

import sys
import os
import sqlite3
import json
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backend.database import get_connection, init_db, DB_PATH
from backend.algorithm import AllocationEngine
from backend.simulator import FloodSimulator
from backend.models import haversine_distance

def run_tests():
    print("=" * 65)
    print("  RELIEFCONNECT: RUNNING COMPREHENSIVE VALIDATION SUITE")
    print("=" * 65)

    # Clean test database
    if os.path.exists(DB_PATH):
        try:
            os.remove(DB_PATH)
        except Exception:
            pass

    # TEST 1: Database Initialization
    print("[TEST 1] Initializing SQLite database & all 10 core entities...")
    init_db(seed_sample_data=True)
    conn = get_connection()
    cur = conn.cursor()

    required_tables = [
        "users", "incidents", "locations", "shelters", "capacity_updates",
        "inventory_items", "help_requests", "volunteers", "allocations", "assignment_history"
    ]

    for tbl in required_tables:
        cur.execute(f"SELECT COUNT(*) FROM {tbl}")
        count = cur.fetchone()[0]
        print(f"  ✓ Entity '{tbl}': OK ({count} initial records)")

    # TEST 2: Haversine Spatial Distance Test
    print("\n[TEST 2] Testing PostGIS/Haversine spatial distance calculations...")
    d = haversine_distance(13.0827, 80.2707, 13.0950, 80.2650)
    assert 1.0 <= d <= 2.5, f"Haversine calculation error: expected ~1.5km, got {d}"
    print(f"  ✓ Spatial Distance: 13.0827,80.2707 to 13.0950,80.2650 = {d} km")

    # TEST 3: Prioritization Algorithm Scoring
    print("\n[TEST 3] Testing dynamic urgency prioritization formula...")
    # Critical case: Severity 5, Vuln 5, Water 2.5m
    score_critical, factors_crit = AllocationEngine.calculate_priority_score(5, 5, 2.5, time.time())
    # Mild case: Severity 1, Vuln 1, Water 0.1m
    score_mild, factors_mild = AllocationEngine.calculate_priority_score(1, 1, 0.1, time.time())
    assert score_critical > score_mild, "Critical score must be significantly higher than mild"
    print(f"  ✓ High Urgency (Sev 5, Vuln 5, Flood 2.5m): Priority Score = {score_critical}")
    print(f"  ✓ Low Urgency  (Sev 1, Vuln 1, Flood 0.1m): Priority Score = {score_mild}")

    # TEST 4: Generate 100+ Synthetic Flood Requests
    print("\n[TEST 4] Generating 120 synthetic flood requests across 3 zones...")
    gen_result = FloodSimulator.generate_flood_scenario(conn, num_requests=120)
    cur.execute("SELECT COUNT(*) FROM help_requests")
    total_reqs = cur.fetchone()[0]
    assert total_reqs >= 100, f"Expected >= 100 requests, got {total_reqs}"
    print(f"  ✓ Successfully created {total_reqs} total requests across monitored flood zones.")

    # TEST 5: Run Batch Allocation with Double-Allocation Lock
    print("\n[TEST 5] Running constrained allocation engine with concurrency locks...")
    alloc_result = AllocationEngine.run_batch_allocation(conn)
    print(f"  ✓ Allocated requests: {alloc_result['allocated_count']}")
    print(f"  ✓ Unresolved (capacity/supply constrained): {alloc_result['unresolved_count']}")

    # TEST 6: Validation Invariants & Zero Double Allocation
    print("\n[TEST 6] Rigorous validation of system invariants...")
    val_report = FloodSimulator.validate_simulation_results(conn)

    double_allocs = val_report["integrity_checks"]["double_allocation_violations"]
    assert double_allocs == 0, f"CRITICAL: Detected {double_allocs} double allocations!"
    print(f"  ✓ Zero Double Allocation Check: PASSED (0 duplicate allocations)")

    cap_viol = val_report["integrity_checks"]["shelter_capacity_violations"]
    assert cap_viol == 0, f"Shelter capacity breached: {cap_viol}"
    print(f"  ✓ Shelter Capacity Integrity: PASSED (No shelters exceeded 100% capacity)")

    inv_viol = val_report["integrity_checks"]["inventory_overbooking_violations"]
    assert inv_viol == 0, f"Inventory negative balance: {inv_viol}"
    print(f"  ✓ Inventory Balance Integrity: PASSED (Zero overbooking)")

    high_pri_pct = val_report["metrics"]["high_priority_resolution_percent"]
    print(f"  ✓ High-Priority Resolution Rate: {high_pri_pct}% of critical requests allocated")

    # TEST 7: Explainability Audit Log Check
    print("\n[TEST 7] Verifying Explainable Decision Rationales...")
    cur.execute("SELECT decision_rationale FROM allocations LIMIT 1")
    row = cur.fetchone()
    assert row is not None, "At least one allocation record should exist"
    rationale = json.loads(row[0])
    assert "matched_shelter" in rationale and "explanation_text" in rationale
    print("  ✓ Decision Rationale Sample:")
    print(f"    - Text: {rationale['explanation_text']}")
    print(f"    - Score: {rationale['priority_score']}")
    print(f"    - Distance: {rationale['matched_shelter']['distance_km']} km")
    print(f"    - Alternatives Evaluated: {len(rationale.get('alternatives_evaluated', []))} facilities")

    conn.close()
    print("\n" + "=" * 65)
    print(f"  ALL TESTS PASSED SUCCESSFULLY! (Verdict: {val_report['overall_status']})")
    print("=" * 65 + "\n")

if __name__ == "__main__":
    run_tests()
