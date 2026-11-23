"""
ReliefConnect Flood Scenario Simulation & Validation Suite
Generates 100+ synthetic disaster requests and rigorously evaluates:
1. Duplicate Prevention Integrity
2. Allocation Fairness across Priority Brackets
3. High-Priority Resolution Rate
"""

import random
import time
import uuid
import json
import sqlite3
from typing import Dict, Any, List
from backend.algorithm import AllocationEngine

class FloodSimulator:
    """Simulates real-world flood emergency scenario and validates allocation invariants."""

    @staticmethod
    def generate_flood_scenario(conn: sqlite3.Connection, num_requests: int = 120) -> Dict[str, Any]:
        """
        Generates 100+ synthetic requests distributed across three realistic flood disaster zones.
        """
        now = time.time()
        cur = conn.cursor()

        # Check for incident
        cur.execute("SELECT id FROM incidents LIMIT 1")
        row = cur.fetchone()
        incident_id = row[0] if row else "inc-flood-2026-01"

        first_names = ["Arun", "Meera", "Vikram", "Sneha", "Kiran", "Deepak", "Ayesha", "Rajesh", "Kavita", "Sanjay", "Anil", "Bhavna", "Gaurav", "Nisha", "Manoj", "Pooja", "Ramesh", "Geeta", "Suresh", "Lakshmi"]
        last_names = ["Kumar", "Patel", "Reddy", "Sharma", "Iyer", "Nair", "Das", "Choudhury", "Menon", "Singh", "Banerjee", "Kapoor", "Pillai", "Verma", "Rao", "Joshi"]

        zones = [
            {
                "name": "Zone A: River Delta Lowlands (Heavy Inundation)",
                "center_lat": 13.090, "center_lon": 80.275,
                "lat_spread": 0.015, "lon_spread": 0.015,
                "water_range": (1.2, 2.9),
                "severity_weights": [1, 2, 8, 20, 35],  # Skewed heavily to 4-5
                "categories": ["rescue_evacuation", "medical_emergency", "rescue_evacuation"]
            },
            {
                "name": "Zone B: Central Urban Residential (Waterlogged Streets)",
                "center_lat": 13.075, "center_lon": 80.260,
                "lat_spread": 0.018, "lon_spread": 0.018,
                "water_range": (0.4, 1.3),
                "severity_weights": [5, 12, 25, 20, 8],
                "categories": ["food_and_water", "shelter_need", "clothing_blankets", "medical_emergency"]
            },
            {
                "name": "Zone C: Southern Isolated Suburbs (Supply Cutoff)",
                "center_lat": 13.055, "center_lon": 80.245,
                "lat_spread": 0.020, "lon_spread": 0.020,
                "water_range": (0.1, 0.7),
                "severity_weights": [10, 25, 30, 10, 5],
                "categories": ["food_and_water", "clothing_blankets", "food_and_water"]
            }
        ]

        created_request_ids = []

        for i in range(num_requests):
            zone = random.choice(zones)
            lat = zone["center_lat"] + random.uniform(-zone["lat_spread"], zone["lat_spread"])
            lon = zone["center_lon"] + random.uniform(-zone["lon_spread"], zone["lon_spread"])
            water_level = round(random.uniform(*zone["water_range"]), 2)
            
            # Severity choice weighted
            severity = random.choices([1, 2, 3, 4, 5], weights=zone["severity_weights"])[0]
            vulnerability = random.choices([1, 2, 3, 4, 5], weights=[15, 20, 25, 25, 15])[0]
            category = random.choice(zone["categories"])

            people = random.choices([1, 2, 3, 4, 5, 6, 8], weights=[15, 25, 25, 20, 8, 5, 2])[0]
            infants = random.choice([0, 1, 2]) if vulnerability >= 3 else 0
            elderly = random.choice([0, 1, 2]) if vulnerability >= 3 else 0

            # Stagger creation times over past 4 hours
            stagger_secs = random.uniform(60, 14400)
            created_at = now - stagger_secs

            contact_name = f"{random.choice(first_names)} {random.choice(last_names)}"
            phone = f"+91-9{random.randint(1000, 9999)}-{random.randint(10000, 99999)}"
            address = f"House #{random.randint(12, 850)}, Street {random.randint(1, 45)}, {zone['name'].split(':')[0]}"

            req_id = f"SIM-REQ-{i+1:04d}"
            loc_id = f"loc-{req_id}"

            # Location with 30% privacy masking
            is_masked = 1 if random.random() < 0.3 else 0
            masked_lat = lat + random.uniform(0.003, 0.007) if is_masked else None
            masked_lon = lon + random.uniform(0.003, 0.007) if is_masked else None

            cur.execute("""
            INSERT INTO locations (id, lat, lon, address_text, district, is_privacy_masked, masked_lat, masked_lon, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (loc_id, lat, lon, address, zone["name"], is_masked, masked_lat, masked_lon, created_at))

            # Initial score calculation
            score, _ = AllocationEngine.calculate_priority_score(severity, vulnerability, water_level, created_at, now)

            notes = []
            if water_level > 1.5:
                notes.append(f"Submerged level: {water_level}m. Stranded on higher ground/roof.")
            if infants > 0:
                notes.append(f"{infants} infant(s) requiring formula/warmth.")
            if elderly > 0:
                notes.append(f"{elderly} elderly person with reduced mobility.")

            cur.execute("""
            INSERT INTO help_requests (
                id, incident_id, requester_id, contact_name, contact_phone, location_id,
                category, severity_level, vulnerability_level, people_count, infants_count,
                elderly_count, medical_urgency_notes, water_level_meters, calculated_priority_score,
                status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                req_id, incident_id, None, contact_name, phone, loc_id,
                category, severity, vulnerability, people, infants, elderly,
                " | ".join(notes) if notes else "Standard relief request",
                water_level, score, "verified", created_at, created_at
            ))
            created_request_ids.append(req_id)

        conn.commit()
        return {
            "total_generated": len(created_request_ids),
            "zones": [z["name"] for z in zones],
            "status": "ready_for_allocation"
        }

    @staticmethod
    def validate_simulation_results(conn: sqlite3.Connection) -> Dict[str, Any]:
        """
        Runs comprehensive audit checks on the allocation results:
        1. Duplicate allocation check (must be 0)
        2. Shelter capacity integrity check
        3. Inventory balance integrity check
        4. High-priority resolution fairness rate
        """
        cur = conn.cursor()

        # Check 1: Duplicate active allocations per request
        cur.execute("""
        SELECT help_request_id, COUNT(*) as alloc_count
        FROM allocations
        WHERE status != 'cancelled'
        GROUP BY help_request_id
        HAVING alloc_count > 1
        """)
        double_allocs = cur.fetchall()
        double_allocation_count = len(double_allocs)

        # Check 2: Shelter capacity violations
        cur.execute("""
        SELECT id, name, total_capacity, current_occupancy
        FROM shelters
        WHERE current_occupancy > total_capacity
        """)
        capacity_violations = [dict(row) for row in cur.fetchall()]

        # Check 3: Inventory negative balance violations
        cur.execute("""
        SELECT id, item_name, quantity_available, quantity_reserved
        FROM inventory_items
        WHERE quantity_reserved > quantity_available
        """)
        inventory_violations = [dict(row) for row in cur.fetchall()]

        # Check 4: Priority vs Resolution Breakdown
        cur.execute("""
        SELECT severity_level,
               COUNT(*) as total_count,
               SUM(CASE WHEN status IN ('allocated', 'in_progress', 'dispatched', 'resolved') THEN 1 ELSE 0 END) as resolved_count
        FROM help_requests
        GROUP BY severity_level
        ORDER BY severity_level DESC
        """)
        severity_stats = []
        for row in cur.fetchall():
            tot = row["total_count"]
            res = row["resolved_count"]
            pct = round((res / tot) * 100.0, 1) if tot > 0 else 0.0
            severity_stats.append({
                "severity_level": row["severity_level"],
                "total": tot,
                "resolved": res,
                "resolution_rate_percent": pct
            })

        # Calculate high-priority resolution (Severity 4 and 5)
        high_sev = [s for s in severity_stats if s["severity_level"] in (4, 5)]
        total_high = sum(s["total"] for s in high_sev)
        resolved_high = sum(s["resolved"] for s in high_sev)
        high_priority_rate = round((resolved_high / total_high) * 100.0, 1) if total_high > 0 else 100.0

        # Overall counts
        cur.execute("SELECT COUNT(*) FROM help_requests")
        total_requests = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM help_requests WHERE status IN ('allocated', 'in_progress', 'dispatched', 'resolved')")
        total_allocated = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM allocations")
        total_allocations_logged = cur.fetchone()[0]

        # Shelter Occupancy Summary
        cur.execute("""
        SELECT id, name, total_capacity, current_occupancy, status
        FROM shelters
        """)
        shelter_stats = [dict(row) for row in cur.fetchall()]

        # Determine validation result status
        integrity_passed = (double_allocation_count == 0) and (len(capacity_violations) == 0) and (len(inventory_violations) == 0)
        fairness_passed = (high_priority_rate >= 75.0)  # High priority requests prioritized over lower severity

        return {
            "validation_timestamp": time.time(),
            "overall_status": "PASSED" if (integrity_passed and fairness_passed) else "WARNING",
            "integrity_checks": {
                "double_allocation_violations": double_allocation_count,
                "double_allocation_passed": (double_allocation_count == 0),
                "shelter_capacity_violations": len(capacity_violations),
                "inventory_overbooking_violations": len(inventory_violations)
            },
            "metrics": {
                "total_requests": total_requests,
                "total_allocated": total_allocated,
                "total_unresolved": total_requests - total_allocated,
                "allocation_rate_percent": round((total_allocated / total_requests) * 100.0, 1) if total_requests > 0 else 0,
                "high_priority_resolution_percent": high_priority_rate,
                "severity_breakdown": severity_stats
            },
            "shelter_utilization": shelter_stats,
            "evaluation_verdict": (
                "Validation Success: Zero duplicate allocations verified across concurrent resources. "
                f"High-priority requests (Level 4-5) received {high_priority_rate}% prioritization under resource constraints."
                if integrity_passed else "Integrity Warning: Constraint violations detected."
            )
        }
