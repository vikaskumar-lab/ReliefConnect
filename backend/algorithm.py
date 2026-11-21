"""
ReliefConnect Advanced Prioritization & Constrained Spatial Allocation Engine
Disaster Relief Coordination Prototype

Features:
- Dynamic Multi-Factor Urgency Scoring with wait-time penalty
- Spatial distance calculation with capacity & resource constraint matching
- Strict Concurrency Protection preventing double-allocation via SQLite transactions
- Explainable Decision Generation (transparent audit trails explaining 'why')
- Dynamic condition re-allocation (shelter evacuation / capacity shocks)
"""

import math
import time
import json
import uuid
import sqlite3
from typing import Dict, List, Any, Optional, Tuple
from backend.models import haversine_distance, RequestCategory, RequestStatus, ShelterStatus

class AllocationEngine:
    """Core allocation algorithm engine for ReliefConnect."""

    @staticmethod
    def calculate_priority_score(
        severity: int,
        vulnerability: int,
        water_level_m: float,
        created_at: float,
        now: Optional[float] = None
    ) -> Tuple[float, Dict[str, float]]:
        """
        Calculates dynamic priority score P_i in range [1.0, 10.0+].
        Formula:
          Severity (50%) + Vulnerability (30%) + Flood Hazard (15%) + Wait Decay (5%)
        """
        if now is None:
            now = time.time()
        
        wait_hours = max(0.0, (now - created_at) / 3600.0)
        
        # Sub-scores
        w_sev = 0.50 * severity
        w_vuln = 0.30 * vulnerability
        w_water = 0.15 * min(water_level_m, 3.0) * 1.667  # normalized 0-5
        w_wait = 0.05 * math.log2(1.0 + wait_hours * 2.0) * 2.0 # dynamic wait decay

        total_score = round(w_sev + w_vuln + w_water + w_wait, 3)
        return total_score, {
            "severity_weight": round(w_sev, 3),
            "vulnerability_weight": round(w_vuln, 3),
            "water_hazard_weight": round(w_water, 3),
            "wait_decay_weight": round(w_wait, 3),
            "wait_hours": round(wait_hours, 2)
        }

    @staticmethod
    def run_batch_allocation(conn: sqlite3.Connection, incident_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Pulls all verified (ready-for-allocation) requests and executes
        constrained optimal allocation with double-allocation locking and explainability.
        """
        now = time.time()
        cur = conn.cursor()

        # 1. Fetch pending requests
        query = """
        SELECT r.id, r.incident_id, r.contact_name, r.contact_phone, r.category,
               r.severity_level, r.vulnerability_level, r.people_count, r.infants_count,
               r.elderly_count, r.medical_urgency_notes, r.water_level_meters, r.created_at,
               l.lat, l.lon, l.address_text
        FROM help_requests r
        JOIN locations l ON r.location_id = l.id
        WHERE r.status = 'verified'
        ORDER BY r.severity_level DESC, r.created_at ASC
        """
        cur.execute(query)
        requests = cur.fetchall()

        if not requests:
            return {"status": "ok", "message": "No pending requests to allocate.", "allocated_count": 0, "unresolved_count": 0, "results": []}

        # 2. Score and sort all requests dynamically
        scored_requests = []
        for req in requests:
            score, factors = AllocationEngine.calculate_priority_score(
                req["severity_level"],
                req["vulnerability_level"],
                req["water_level_meters"],
                req["created_at"],
                now
            )
            # Update score in database
            cur.execute("UPDATE help_requests SET calculated_priority_score = ? WHERE id = ?", (score, req["id"]))
            scored_requests.append({
                "data": req,
                "score": score,
                "factors": factors
            })

        # Sort descending by priority score
        scored_requests.sort(key=lambda x: x["score"], reverse=True)

        allocations_made = []
        unresolved_requests = []

        # 3. Process requests sequentially with strict database-level reservation guarantees
        for item in scored_requests:
            req = item["data"]
            req_id = req["id"]
            req_lat = req["lat"]
            req_lon = req["lon"]
            people = req["people_count"]
            category = req["category"]
            water_level = req["water_level_meters"]

            # Evaluate candidate shelters
            cur.execute("""
            SELECT s.id, s.name, s.total_capacity, s.current_occupancy, s.status, s.has_medical_unit,
                   l.lat, l.lon, l.address_text,
                   haversine_distance(?, ?, l.lat, l.lon) as distance_km
            FROM shelters s
            JOIN locations l ON s.location_id = l.id
            WHERE s.status = 'open'
            ORDER BY distance_km ASC
            """, (req_lat, req_lon))
            candidate_shelters = cur.fetchall()

            allocated_shelter = None
            alternatives_evaluated = []
            assigned_volunteer = None

            # Determine needs
            needs_bed = category in ("shelter_need", "rescue_evacuation")
            needs_supplies = category in ("food_and_water", "clothing_blankets", "medical_emergency")

            # Find matching shelter
            for shl in candidate_shelters:
                dist = shl["distance_km"]
                avail_beds = shl["total_capacity"] - shl["current_occupancy"]

                eval_entry = {
                    "shelter_id": shl["id"],
                    "shelter_name": shl["name"],
                    "distance_km": dist,
                    "available_beds": avail_beds
                }

                if needs_bed and avail_beds < people:
                    eval_entry["decision"] = "REJECTED"
                    eval_entry["reason"] = f"Insufficient bed capacity (Requires {people}, available {avail_beds})"
                    alternatives_evaluated.append(eval_entry)
                    continue

                # Check inventory if supplies are needed
                supplies_ok = True
                if needs_supplies:
                    # check if shelter has at least some supplies
                    cur.execute("""
                    SELECT id, category, quantity_available, quantity_reserved
                    FROM inventory_items
                    WHERE shelter_id = ? AND quantity_available - quantity_reserved > 0
                    """, (shl["id"],))
                    inv_rows = cur.fetchall()
                    if not inv_rows:
                        eval_entry["decision"] = "REJECTED"
                        eval_entry["reason"] = "No available inventory stock at this facility."
                        alternatives_evaluated.append(eval_entry)
                        continue

                # Candidate is suitable!
                eval_entry["decision"] = "SELECTED"
                eval_entry["reason"] = f"Optimal fit: {dist:.2f}km away, {avail_beds} beds free."
                alternatives_evaluated.append(eval_entry)
                allocated_shelter = shl
                break

            if not allocated_shelter:
                unresolved_requests.append({
                    "request_id": req_id,
                    "contact_name": req["contact_name"],
                    "priority_score": item["score"],
                    "reason": "All active shelters within reach are at full capacity or exhausted resources.",
                    "alternatives_evaluated": alternatives_evaluated
                })
                continue

            # ATOMIC RESERVATION TRANSACTION (Prevents Double Allocation)
            try:
                # 1. Update shelter occupancy atomically
                beds_to_allocate = people if needs_bed else 0
                if beds_to_allocate > 0:
                    cur.execute("""
                    UPDATE shelters
                    SET current_occupancy = current_occupancy + ?,
                        status = CASE WHEN current_occupancy + ? >= total_capacity THEN 'at_capacity' ELSE 'open' END,
                        updated_at = ?
                    WHERE id = ? AND (current_occupancy + ?) <= total_capacity
                    """, (beds_to_allocate, beds_to_allocate, now, allocated_shelter["id"], beds_to_allocate))

                    if cur.rowcount == 0:
                        # Concurrency clash: another thread took the last beds
                        unresolved_requests.append({
                            "request_id": req_id,
                            "contact_name": req["contact_name"],
                            "reason": "Concurrency Conflict: Shelter beds were reserved by concurrent high-priority claim.",
                            "alternatives_evaluated": alternatives_evaluated
                        })
                        continue

                    # Log capacity update audit
                    cur.execute("""
                    INSERT INTO capacity_updates (id, shelter_id, previous_occupancy, new_occupancy, delta_change, reason, updated_by, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        str(uuid.uuid4()), allocated_shelter["id"],
                        allocated_shelter["current_occupancy"],
                        allocated_shelter["current_occupancy"] + beds_to_allocate,
                        beds_to_allocate, f"Allocation for Request {req_id}", "usr-algorithm-engine", now
                    ))

                # 2. Allocate inventory supplies if required
                allocated_inventory = []
                if category == "food_and_water":
                    target_categories = ["food_rations", "drinking_water"]
                elif category == "medical_emergency":
                    target_categories = ["medical_supplies"]
                elif category == "clothing_blankets":
                    target_categories = ["clothing_blankets"]
                else:
                    target_categories = ["drinking_water"]

                for t_cat in target_categories:
                    cur.execute("""
                    SELECT id, item_name, quantity_available, quantity_reserved, unit
                    FROM inventory_items
                    WHERE shelter_id = ? AND category = ?
                    LIMIT 1
                    """, (allocated_shelter["id"], t_cat))
                    inv_item = cur.fetchone()
                    if inv_item:
                        qty_needed = people if t_cat != "drinking_water" else people * 2
                        avail_qty = inv_item["quantity_available"] - inv_item["quantity_reserved"]
                        qty_to_assign = min(qty_needed, max(1, avail_qty))

                        if qty_to_assign > 0:
                            # Atomic inventory reservation
                            cur.execute("""
                            UPDATE inventory_items
                            SET quantity_reserved = quantity_reserved + ?,
                                updated_at = ?
                            WHERE id = ? AND (quantity_available - quantity_reserved) >= ?
                            """, (qty_to_assign, now, inv_item["id"], qty_to_assign))

                            if cur.rowcount > 0:
                                allocated_inventory.append({
                                    "item_id": inv_item["id"],
                                    "item_name": inv_item["item_name"],
                                    "category": t_cat,
                                    "quantity": qty_to_assign,
                                    "unit": inv_item["unit"]
                                })

                # 3. Match best available volunteer based on skills and water hazard
                matched_volunteer = None
                if water_level > 1.2:
                    required_skill = "boat_rescue"
                elif category == "medical_emergency":
                    required_skill = "first_aid"
                else:
                    required_skill = None

                cur.execute("""
                SELECT v.id, u.full_name, v.skills_json, v.status,
                       haversine_distance(?, ?, l.lat, l.lon) as distance_km
                FROM volunteers v
                JOIN users u ON v.user_id = u.id
                JOIN locations l ON v.location_id = l.id
                WHERE v.status = 'available'
                ORDER BY distance_km ASC
                """, (req_lat, req_lon))
                vol_candidates = cur.fetchall()

                for v in vol_candidates:
                    skills = json.loads(v["skills_json"])
                    if required_skill is None or required_skill in skills:
                        matched_volunteer = {
                            "id": v["id"],
                            "name": v["full_name"],
                            "skills": skills,
                            "distance_km": v["distance_km"],
                            "skill_matched": required_skill or "general_dispatch"
                        }
                        # Mark volunteer assigned
                        cur.execute("UPDATE volunteers SET status = 'assigned' WHERE id = ?", (v["id"],))
                        # Create assignment history record
                        cur.execute("""
                        INSERT INTO assignment_history (id, volunteer_id, help_request_id, assigned_by, instructions, assigned_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """, (
                            str(uuid.uuid4()), v["id"], req_id, "usr-algorithm-engine",
                            f"Deploy to {req['contact_name']} at {req['address_text']}. Special note: water depth {water_level}m.",
                            now
                        ))
                        break

                # 4. Compile Explainable Decision Rationale
                rationale = {
                    "timestamp": now,
                    "request_id": req_id,
                    "priority_score": item["score"],
                    "score_breakdown": item["factors"],
                    "matched_shelter": {
                        "id": allocated_shelter["id"],
                        "name": allocated_shelter["name"],
                        "distance_km": allocated_shelter["distance_km"],
                        "beds_reserved": beds_to_allocate
                    },
                    "allocated_supplies": allocated_inventory,
                    "assigned_volunteer": matched_volunteer,
                    "alternatives_evaluated": alternatives_evaluated,
                    "explanation_text": (
                        f"Request from {req['contact_name']} (Priority Score {item['score']}) was matched with "
                        f"{allocated_shelter['name']} ({allocated_shelter['distance_km']:.2f} km away). "
                        f"Allocated {beds_to_allocate} shelter beds and {len(allocated_inventory)} supply item types. "
                        + (f"Dispatched volunteer {matched_volunteer['name']} with '{matched_volunteer['skill_matched']}' skill." if matched_volunteer else "No field volunteer currently available; shelter staging team notified.")
                    )
                }

                # 5. Insert Allocation Record
                alloc_id = f"ALC-{str(uuid.uuid4())[:8].upper()}"
                primary_item_id = allocated_inventory[0]["item_id"] if allocated_inventory else None
                cur.execute("""
                INSERT INTO allocations (
                    id, help_request_id, shelter_id, inventory_item_id,
                    quantity_allocated, beds_allocated, status, allocated_by_algorithm,
                    decision_rationale, allocated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    alloc_id, req_id, allocated_shelter["id"], primary_item_id,
                    sum(x["quantity"] for x in allocated_inventory), beds_to_allocate,
                    "reserved", 1, json.dumps(rationale), now
                ))

                # 6. Update request status
                new_status = "allocated" if matched_volunteer else "allocated"
                cur.execute("UPDATE help_requests SET status = ?, updated_at = ? WHERE id = ?", (new_status, now, req_id))

                allocations_made.append(rationale)

            except Exception as e:
                # Log transaction error
                conn.rollback()
                unresolved_requests.append({
                    "request_id": req_id,
                    "reason": f"Allocation transaction exception: {str(e)}"
                })
                continue

        conn.commit()

        return {
            "status": "ok",
            "allocated_count": len(allocations_made),
            "unresolved_count": len(unresolved_requests),
            "allocations": allocations_made,
            "unresolved": unresolved_requests
        }

    @staticmethod
    def simulate_shelter_failure(conn: sqlite3.Connection, shelter_id: str, reason: str = "Severe facility flooding / structural compromise") -> Dict[str, Any]:
        """
        Simulates dynamic crisis conditions: A shelter experiences an emergency,
        shutting down capacity and triggering immediate emergency evacuation and re-allocation!
        """
        now = time.time()
        cur = conn.cursor()

        # Get shelter details
        cur.execute("SELECT * FROM shelters WHERE id = ?", (shelter_id,))
        shelter = cur.fetchone()
        if not shelter:
            return {"error": "Shelter not found"}

        displaced_occupants = shelter["current_occupancy"]

        # 1. Update shelter status to EVACUATING / CLOSED
        cur.execute("""
        UPDATE shelters
        SET status = 'evacuating', current_occupancy = 0, updated_at = ?
        WHERE id = ?
        """, (now, shelter_id))

        # 2. Log capacity update audit
        cur.execute("""
        INSERT INTO capacity_updates (id, shelter_id, previous_occupancy, new_occupancy, delta_change, reason, updated_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (str(uuid.uuid4()), shelter_id, displaced_occupants, 0, -displaced_occupants, f"EMERGENCY EVACUATION: {reason}", "usr-algorithm-engine", now))

        # 3. Create emergency re-allocation requests for displaced occupants in batches of 4-6
        created_displaced_requests = []
        remaining = displaced_occupants
        batch_idx = 1
        while remaining > 0:
            batch_size = min(remaining, 5)
            remaining -= batch_size
            req_id = f"EVAC-{shelter_id[:6]}-{batch_idx:02d}"
            
            # create location near the failing shelter
            loc_id = f"loc-{req_id}"
            cur.execute("""
            INSERT INTO locations (id, lat, lon, address_text, district, is_privacy_masked, created_at)
            SELECT ?, lat + 0.002, lon + 0.002, 'Evacuee Staging Area outside ' || ?, 'Emergency Sector', 0, ?
            FROM locations WHERE id = ?
            """, (loc_id, shelter["name"], now, shelter["location_id"]))

            cur.execute("""
            INSERT INTO help_requests (
                id, incident_id, contact_name, contact_phone, location_id,
                category, severity_level, vulnerability_level, people_count,
                medical_urgency_notes, water_level_meters, calculated_priority_score,
                status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                req_id, shelter["incident_id"], f"Displaced Group #{batch_idx} ({shelter['name']})", "+91-98400-00911",
                loc_id, "rescue_evacuation", 5, 4, batch_size,
                f"Displaced due to facility shutdown: {reason}", 2.2, 5.5,
                "verified", now, now
            ))
            created_displaced_requests.append(req_id)
            batch_idx += 1

        conn.commit()

        # Run allocation immediately for displaced groups
        realloc_result = AllocationEngine.run_batch_allocation(conn)

        return {
            "status": "evacuated",
            "shelter_id": shelter_id,
            "shelter_name": shelter["name"],
            "displaced_people": displaced_occupants,
            "evacuation_requests_created": len(created_displaced_requests),
            "reallocation_summary": realloc_result
        }
