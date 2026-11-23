"""Prioritization and constrained shelter allocation engine."""

import json
import time
import uuid

from backend.models import haversine_distance


class AllocationEngine:
    @staticmethod
    def calculate_priority_score(severity, vulnerability, water_level, created_at, now):
        wait_hours = max(0.0, (now - created_at) / 3600.0)
        severity = min(5, max(1, severity))
        vulnerability = min(5, max(1, vulnerability))
        water_level = max(0.0, water_level)
        wait_decay = min(1.0, wait_hours / 24.0)
        score = (
            0.50 * severity
            + 0.30 * vulnerability
            + 0.15 * min(5.0, water_level)
            + 0.05 * wait_decay
        )
        breakdown = {
            "severity": 0.50 * severity,
            "vulnerability": 0.30 * vulnerability,
            "water_level": 0.15 * min(5.0, water_level),
            "wait_decay": 0.05 * wait_decay,
        }
        return round(score, 4), breakdown

    @staticmethod
    def run_batch_allocation(conn):
        conn.execute("BEGIN IMMEDIATE")
        allocated_count = 0
        unresolved_count = 0

        requests = conn.execute("""
            SELECT r.*, l.lat, l.lon
            FROM help_requests r
            JOIN locations l ON r.location_id = l.id
            WHERE r.status IN ('submitted', 'verified')
            ORDER BY r.calculated_priority_score DESC, r.created_at ASC
        """).fetchall()

        shelters = conn.execute("""
            SELECT s.*, l.lat, l.lon
            FROM shelters s
            JOIN locations l ON s.location_id = l.id
            WHERE s.status = 'open'
        """).fetchall()

        for request in requests:
            people_needed = max(1, request["people_count"])
            eligible = [
                shelter for shelter in shelters
                if shelter["total_capacity"] - shelter["current_occupancy"] >= people_needed
            ]
            if not eligible:
                unresolved_count += 1
                continue

            shelter = min(
                eligible,
                key=lambda item: haversine_distance(
                    request["lat"], request["lon"], item["lat"], item["lon"]
                ),
            )
            distance = haversine_distance(
                request["lat"], request["lon"], shelter["lat"], shelter["lon"]
            )
            now = time.time()
            allocation_id = str(uuid.uuid4())
            rationale = {
                "priority_score": request["calculated_priority_score"],
                "distance_km": round(distance, 2),
                "people_count": people_needed,
                "reason": "Nearest shelter with sufficient bed capacity",
            }
            conn.execute("""
                INSERT INTO allocations (
                    id, help_request_id, shelter_id, quantity_allocated,
                    beds_allocated, status, allocated_by_algorithm,
                    decision_rationale, allocated_at
                ) VALUES (?, ?, ?, 0, ?, 'reserved', 1, ?, ?)
            """, (
                allocation_id,
                request["id"],
                shelter["id"],
                people_needed,
                json.dumps(rationale),
                now,
            ))
            conn.execute("""
                UPDATE shelters
                SET current_occupancy = current_occupancy + ?, updated_at = ?
                WHERE id = ?
            """, (people_needed, now, shelter["id"]))
            conn.execute("""
                UPDATE help_requests
                SET status = 'allocated', resolved_at = NULL, updated_at = ?
                WHERE id = ?
            """, (now, request["id"]))
            shelter_id = shelter["id"]
            shelter_index = next(i for i, item in enumerate(shelters) if item["id"] == shelter_id)
            shelters[shelter_index]["current_occupancy"] += people_needed
            allocated_count += 1

        conn.commit()
        return {
            "allocated_count": allocated_count,
            "unresolved_count": unresolved_count,
            "total_considered": len(requests),
        }

    @staticmethod
    def simulate_shelter_failure(conn, shelter_id, reason):
        conn.execute("BEGIN IMMEDIATE")
        shelter = conn.execute(
            "SELECT * FROM shelters WHERE id = ?", (shelter_id,)
        ).fetchone()
        if shelter is None:
            conn.commit()
            return {"success": False, "error": "Shelter not found"}

        now = time.time()
        conn.execute(
            "UPDATE shelters SET status = 'closed', updated_at = ? WHERE id = ?",
            (now, shelter_id),
        )
        conn.execute("""
            UPDATE allocations
            SET status = 'cancelled', completed_at = ?
            WHERE shelter_id = ? AND status != 'cancelled'
        """, (now, shelter_id))
        conn.execute("""
            UPDATE help_requests
            SET status = 'submitted', updated_at = ?
            WHERE id IN (
                SELECT help_request_id FROM allocations
                WHERE shelter_id = ? AND status = 'cancelled'
            )
        """, (now, shelter_id))
        conn.commit()
        return {
            "success": True,
            "shelter_id": shelter_id,
            "reason": reason,
            "status": "closed",
        }
