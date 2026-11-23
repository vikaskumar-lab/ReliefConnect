"""Synthetic flood scenario generator and validation."""

import random
import time
import uuid


class FloodSimulator:
    @staticmethod
    def generate_flood_scenario(conn, num_requests=120):
        now = time.time()
        incident = conn.execute(
            "SELECT id FROM incidents WHERE status = 'active' LIMIT 1"
        ).fetchone()
        if incident is None:
            return {"total_generated": 0, "error": "No active incident"}

        generated = 0
        for index in range(num_requests):
            lat = 13.0500 + random.random() * 0.0600
            lon = 80.2450 + random.random() * 0.0550
            location_id = str(uuid.uuid4())
            conn.execute("""
                INSERT INTO locations (
                    id, lat, lon, address_text, district,
                    is_privacy_masked, masked_lat, masked_lon, created_at
                ) VALUES (?, ?, ?, ?, ?, 0, NULL, NULL, ?)
            """, (
                location_id,
                lat,
                lon,
                f"Synthetic Flood Zone {index + 1}",
                "Simulation District",
                now,
            ))

            severity = random.randint(2, 5)
            vulnerability = random.randint(1, 5)
            water_level = round(random.uniform(0.2, 4.5), 2)
            people_count = random.randint(1, 8)
            from backend.algorithm import AllocationEngine
            priority, _ = AllocationEngine.calculate_priority_score(
                severity, vulnerability, water_level, now - random.randint(0, 12 * 3600), now
            )
            conn.execute("""
                INSERT INTO help_requests (
                    id, incident_id, requester_id, contact_name, contact_phone,
                    location_id, category, severity_level, vulnerability_level,
                    people_count, infants_count, elderly_count, medical_urgency_notes,
                    water_level_meters, calculated_priority_score, status,
                    created_at, updated_at
                ) VALUES (?, ?, NULL, ?, ?, ?, 'evacuation', ?, ?, ?, ?, ?, '', ?, ?, 'submitted', ?, ?)
            """, (
                str(uuid.uuid4()),
                incident["id"],
                f"Simulation Citizen {index + 1}",
                f"+91-90000-{index:05d}",
                location_id,
                severity,
                vulnerability,
                people_count,
                1 if random.random() < 0.25 else 0,
                1 if random.random() < 0.30 else 0,
                water_level,
                priority,
                now,
                now,
            ))
            generated += 1
        conn.commit()
        return {"total_generated": generated}

    @staticmethod
    def validate_simulation_results(conn):
        duplicate_allocations = conn.execute("""
            SELECT COUNT(*) FROM (
                SELECT help_request_id
                FROM allocations
                WHERE status != 'cancelled'
                GROUP BY help_request_id
                HAVING COUNT(*) > 1
            )
        """).fetchone()[0]
        overdue_capacity = conn.execute("""
            SELECT COUNT(*) FROM shelters
            WHERE current_occupancy > total_capacity
        """).fetchone()[0]
        allocated = conn.execute(
            "SELECT COUNT(*) FROM allocations WHERE status != 'cancelled'"
        ).fetchone()[0]
        pending = conn.execute("""
            SELECT COUNT(*) FROM help_requests
            WHERE status IN ('submitted', 'verified')
        """).fetchone()[0]
        return {
            "duplicate_allocations": duplicate_allocations,
            "shelter_capacity_violations": overdue_capacity,
            "allocated_requests": allocated,
            "pending_requests": pending,
            "valid": duplicate_allocations == 0 and overdue_capacity == 0,
        }
