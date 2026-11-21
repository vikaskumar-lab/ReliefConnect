"""
ReliefConnect baseline help-request seed data.

Creates a realistic mix of requests in every workflow status so each dashboard
filter (Submitted / Verified / Allocated / Resolved) has records to show, and the
headline statistics always match the table because both come from the same rows.
"""

import json
import sqlite3
import time
import uuid
from typing import Any, Dict, List, Tuple

from backend.algorithm import AllocationEngine
from backend.models import haversine_distance

COORD_USER = "usr-coord-01"
ALGO_USER = "usr-algorithm-engine"

# (status, hours_ago_created, name, phone, address, district, lat, lon, category,
#  severity, vulnerability, people, infants, elderly, water_m, masked, notes)
RAW_REQUESTS: List[Tuple] = [
    # ---------------- SUBMITTED (pending verification) ----------------
    ("submitted", 0.4, "Lakshmi Narayanan", "+91-98111-30101", "22 Canal Bank Road, Near Pump House", "Riverside Lowlands", 13.0892, 80.2764, "rescue_evacuation", 5, 5, 5, 1, 1, 2.1, 1, "Ground floor submerged. Family moved to terrace. Infant is unwell."),
    ("submitted", 0.9, "Mohammed Farooq", "+91-98111-30102", "Plot 7, Fisherman Colony", "East Coastline", 13.0748, 80.2866, "medical_emergency", 4, 4, 3, 0, 1, 1.1, 0, "Diabetic mother has run out of insulin. Road to clinic is cut off."),
    ("submitted", 1.3, "Revathi Subramanian", "+91-98111-30103", "5th Cross, Teachers Colony", "Central Urban", 13.0786, 80.2688, "food_and_water", 3, 3, 6, 2, 0, 0.5, 0, "No drinking water since last night. Two toddlers in the house."),
    ("submitted", 1.8, "Arjun Pillai", "+91-98111-30104", "Block D, Mill Workers Quarters", "Central Urban", 13.0701, 80.2625, "shelter_need", 3, 2, 4, 0, 0, 0.8, 0, "Rented room flooded, need dry place for family tonight."),
    ("submitted", 2.2, "Zainab Begum", "+91-98111-30105", "31 Tannery Lane", "Riverside Lowlands", 13.0915, 80.2722, "rescue_evacuation", 5, 4, 7, 1, 2, 1.9, 1, "Water above waist height. Two elderly members cannot walk."),
    ("submitted", 2.7, "Gopal Krishnan", "+91-98111-30106", "House 18, Lakeview Layout", "South Suburbs", 13.0562, 80.2488, "clothing_blankets", 2, 2, 3, 0, 0, 0.3, 0, "Clothes and bedding soaked. Need dry blankets."),
    ("submitted", 3.1, "Deepa Menon", "+91-98111-30107", "Flat 4B, Sunrise Apartments", "South Suburbs", 13.0530, 80.2550, "food_and_water", 2, 3, 2, 1, 0, 0.2, 0, "Power cut for 20 hours. Baby formula and water needed."),
    ("submitted", 3.6, "Sundar Rajan", "+91-98111-30108", "Behind Old Bus Depot", "Central Urban", 13.0834, 80.2790, "medical_emergency", 4, 3, 2, 0, 1, 0.9, 0, "Elderly neighbour with chest pain. Ambulance cannot reach the lane."),

    # ---------------- VERIFIED (ready for allocation) ----------------
    ("verified", 4.0, "Ananya Sharma", "+91-98111-22201", "14 Riverside Slums, Lowland Area", "Riverside Lowlands", 13.0865, 80.2750, "rescue_evacuation", 5, 4, 4, 1, 1, 1.8, 1, "Water rising rapidly. Trapped on rooftop with infant."),
    ("verified", 4.6, "Karthik Raja", "+91-98111-22202", "90 Market Road, Ground Floor", "Central Urban", 13.0720, 80.2600, "medical_emergency", 4, 3, 2, 0, 1, 0.6, 0, "Elderly father needs urgent dialysis and insulin storage."),
    ("verified", 5.1, "Sunita Patel", "+91-98111-22203", "Block C, Labour Colony", "South Suburbs", 13.0600, 80.2580, "food_and_water", 3, 3, 6, 2, 0, 0.2, 0, "No clean drinking water for 36 hours. 6 people including toddlers."),
    ("verified", 5.5, "Venkatesh Iyer", "+91-98111-30201", "9 Temple Street", "Central Urban", 13.0805, 80.2712, "shelter_need", 4, 4, 5, 1, 2, 1.0, 0, "House damaged by flooding. Two elderly members on oxygen support."),
    ("verified", 6.0, "Shabana Khan", "+91-98111-30202", "Gali 3, Old Market Area", "Riverside Lowlands", 13.0880, 80.2705, "rescue_evacuation", 5, 5, 3, 1, 0, 2.4, 1, "Neck-deep water in the lane. Mother and newborn stranded upstairs."),
    ("verified", 6.4, "Prakash Nair", "+91-98111-30203", "27 Harbour View Road", "East Coastline", 13.0731, 80.2851, "clothing_blankets", 3, 3, 4, 1, 0, 0.7, 0, "Family of four, soaked and cold. Child has a fever."),
    ("verified", 7.0, "Meenakshi Sundaram", "+91-98111-30204", "Door 61, Kamaraj Nagar", "Central Urban", 13.0771, 80.2643, "food_and_water", 3, 2, 8, 0, 1, 0.4, 0, "Joint family of 8 without rations. Shops closed."),
    ("verified", 7.5, "Irfan Ahmed", "+91-98111-30205", "Ward 12, Pump Station Road", "South Suburbs", 13.0575, 80.2530, "medical_emergency", 4, 4, 2, 0, 1, 0.5, 0, "Dialysis patient missed session. Needs transport and medical kit."),

    # ---------------- ALLOCATED (shelter + supplies reserved) ----------------
    ("allocated", 8.0, "Rukmini Devi", "+91-98111-30301", "Gate 2, Riverbend Housing", "Riverside Lowlands", 13.0905, 80.2655, "rescue_evacuation", 5, 4, 5, 1, 1, 1.6, 1, "Evacuation requested. Boat team assigned."),
    ("allocated", 8.6, "Harish Babu", "+91-98111-30302", "44 Garden Street", "Central Urban", 13.0815, 80.2735, "shelter_need", 4, 3, 3, 0, 1, 0.9, 0, "Moved to shelter after ground floor flooded."),
    ("allocated", 9.1, "Nandini Rao", "+91-98111-30303", "17 School Road", "South Suburbs", 13.0555, 80.2510, "food_and_water", 3, 3, 5, 1, 0, 0.3, 0, "Rations and water reserved at nearest depot."),
    ("allocated", 9.8, "Imran Sheikh", "+91-98111-30304", "Lane 5, Fish Market", "East Coastline", 13.0745, 80.2872, "medical_emergency", 4, 4, 2, 0, 1, 0.7, 0, "Trauma kit and first-aid volunteer assigned."),
    ("allocated", 10.4, "Saraswathi Iyer", "+91-98111-30305", "8 Library Road", "Central Urban", 13.0822, 80.2760, "clothing_blankets", 2, 2, 3, 0, 0, 0.3, 0, "Blankets reserved for pickup."),

    # ---------------- RESOLVED (completed) ----------------
    ("resolved", 11.0, "Balaji Chandran", "+91-98111-30401", "12 Dock Road", "East Coastline", 13.0738, 80.2845, "rescue_evacuation", 5, 4, 4, 1, 1, 2.0, 1, "Family evacuated by boat and settled at shelter."),
    ("resolved", 11.4, "Fathima Rahman", "+91-98111-30402", "Plot 3, Mosque Street", "Central Urban", 13.0797, 80.2702, "medical_emergency", 4, 4, 2, 0, 1, 0.8, 0, "Insulin and first-aid delivered. Patient stable."),
    ("resolved", 11.9, "Gnanasekaran", "+91-98111-30403", "Ward 9, Cement Colony", "South Suburbs", 13.0588, 80.2562, "food_and_water", 3, 3, 6, 2, 0, 0.3, 0, "Rations and water delivered to household."),
    ("resolved", 12.3, "Jayanthi Krishnan", "+91-98111-30404", "55 Lotus Pond Lane", "North Ridge", 13.0940, 80.2640, "shelter_need", 3, 3, 4, 1, 0, 0.6, 0, "Housed at North Sector Indoor Stadium."),
    ("resolved", 12.8, "Kabir Hussain", "+91-98111-30405", "Lane 2, Weavers Colony", "Riverside Lowlands", 13.0870, 80.2690, "rescue_evacuation", 5, 5, 3, 1, 1, 1.7, 1, "Elderly couple and infant rescued from first floor."),
    ("resolved", 13.2, "Latha Mahesh", "+91-98111-30406", "21 Station Road", "Central Urban", 13.0828, 80.2778, "clothing_blankets", 2, 2, 3, 0, 0, 0.2, 0, "Blankets and dry clothes distributed."),
    ("resolved", 13.6, "Manickam Pillai", "+91-98111-30407", "Hut 14, Canal Side", "Riverside Lowlands", 13.0898, 80.2740, "food_and_water", 3, 3, 5, 1, 1, 1.0, 0, "Food packets and water delivered by volunteer."),
    ("resolved", 14.0, "Nargis Fatima", "+91-98111-30408", "6 Orchard Road", "South Suburbs", 13.0540, 80.2495, "medical_emergency", 4, 3, 2, 0, 1, 0.4, 0, "First-aid kit and nurse visit completed."),
]

NEED_BED = ("shelter_need", "rescue_evacuation")
SUPPLY_MAP = {
    "food_and_water": ["food_rations", "drinking_water"],
    "medical_emergency": ["medical_supplies"],
    "clothing_blankets": ["clothing_blankets"],
}


def _audit(conn: sqlite3.Connection, entity_type: str, entity_id: str, action: str,
           details: Dict[str, Any], actor: str, ts: float) -> None:
    conn.execute(
        "INSERT INTO audit_log (id, entity_type, entity_id, action, details_json, performed_by, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), entity_type, entity_id, action, json.dumps(details), actor, ts),
    )


def seed_help_requests(conn: sqlite3.Connection, incident_id: str, now: float) -> None:
    cur = conn.cursor()

    shelters = [dict(r) for r in cur.execute(
        "SELECT s.id, s.name, s.total_capacity, s.current_occupancy, l.lat, l.lon "
        "FROM shelters s JOIN locations l ON s.location_id = l.id").fetchall()]
    volunteers = [dict(r) for r in cur.execute(
        "SELECT v.id, u.full_name, v.skills_json, l.lat, l.lon FROM volunteers v "
        "JOIN users u ON v.user_id = u.id JOIN locations l ON v.location_id = l.id "
        "ORDER BY v.id").fetchall()]
    free_volunteers = list(volunteers)

    # Oldest first so IDs increase with time: REQ-2026-0001 is the oldest request.
    rows = sorted(RAW_REQUESTS, key=lambda r: -r[1])

    for seq, row in enumerate(rows, start=1):
        (status, hrs, name, phone, address, district, lat, lon, category,
         sev, vuln, people, infants, elderly, water, masked, notes) = row
        req_id = f"REQ-2026-{seq:04d}"
        created = now - hrs * 3600
        loc_id = f"loc-{req_id}"
        cur.execute(
            "INSERT INTO locations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (loc_id, lat, lon, address, district, masked,
             lat + 0.004 if masked else None, lon + 0.004 if masked else None, created))

        # Same scoring function the live allocation engine uses.
        score_at = created if status in ("submitted",) else now
        score, factors = AllocationEngine.calculate_priority_score(sev, vuln, water, created, score_at)

        verified_at = created + 600 if status != "submitted" else None
        resolved_at = created + 4 * 3600 if status == "resolved" else None
        cur.execute("""
            INSERT INTO help_requests (
                id, incident_id, requester_id, contact_name, contact_phone, location_id,
                category, severity_level, vulnerability_level, people_count, infants_count,
                elderly_count, medical_urgency_notes, water_level_meters, calculated_priority_score,
                status, verified_by, verified_at, resolved_at, notes, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (req_id, incident_id, None, name, phone, loc_id, category, sev, vuln, people,
              infants, elderly, notes, water, score, status,
              COORD_USER if verified_at else None, verified_at, resolved_at,
              None, created, resolved_at or verified_at or created))

        _audit(conn, "help_request", req_id, "REQUEST_SUBMITTED",
               {"contact_name": name, "category": category, "severity": sev,
                "people": people, "priority_score": score}, "Citizen", created)
        if verified_at:
            _audit(conn, "help_request", req_id, "REQUEST_VERIFIED",
                   {"verified_by": "Sarah Jenkins", "note": "Caller identity and location confirmed"},
                   "Sarah Jenkins", verified_at)

        if status not in ("allocated", "resolved"):
            continue

        # ---- build a real allocation for allocated / resolved requests ----
        ranked = sorted(shelters, key=lambda s: haversine_distance(lat, lon, s["lat"], s["lon"]))
        chosen = ranked[0]
        needs_bed = category in NEED_BED
        beds = people if needs_bed else 0
        alternatives = []
        for s in ranked:
            d = haversine_distance(lat, lon, s["lat"], s["lon"])
            free = s["total_capacity"] - s["current_occupancy"]
            alternatives.append({
                "shelter_id": s["id"], "shelter_name": s["name"], "distance_km": d,
                "available_beds": free,
                "decision": "SELECTED" if s is chosen else "NOT_EVALUATED",
                "reason": (f"Optimal fit: {d:.2f}km away, {free} beds free."
                           if s is chosen else "A closer shelter already satisfied the request."),
            })

        supplies = []
        for cat in SUPPLY_MAP.get(category, ["drinking_water"] if needs_bed else []):
            inv = cur.execute(
                "SELECT id, item_name, unit, quantity_available FROM inventory_items "
                "WHERE shelter_id = ? AND category = ?", (chosen["id"], cat)).fetchone()
            if inv:
                qty = min(people * (2 if cat == "drinking_water" else 1), inv["quantity_available"])
                supplies.append({"item_id": inv["id"], "item_name": inv["item_name"],
                                 "category": cat, "quantity": qty, "unit": inv["unit"]})
                if status == "allocated":  # resolved = already delivered, stock consumed
                    cur.execute("UPDATE inventory_items SET quantity_reserved = quantity_reserved + ? WHERE id = ?",
                                (qty, inv["id"]))

        if status == "allocated" and beds:
            cur.execute("UPDATE shelters SET current_occupancy = current_occupancy + ? WHERE id = ?", (beds, chosen["id"]))
            cur.execute("INSERT INTO capacity_updates (id, shelter_id, previous_occupancy, new_occupancy, delta_change, reason, updated_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (str(uuid.uuid4()), chosen["id"], chosen["current_occupancy"], chosen["current_occupancy"] + beds,
                         beds, f"Allocation for Request {req_id}", ALGO_USER, verified_at + 300))
            chosen["current_occupancy"] += beds

        vol = None
        if free_volunteers and status == "allocated" and (water > 1.2 or category == "medical_emergency"):
            need = "boat_rescue" if water > 1.2 else "first_aid"
            for v in free_volunteers:
                if need in json.loads(v["skills_json"]):
                    vol = {"id": v["id"], "name": v["full_name"], "skills": json.loads(v["skills_json"]),
                           "distance_km": haversine_distance(lat, lon, v["lat"], v["lon"]),
                           "skill_matched": need}
                    free_volunteers.remove(v)
                    cur.execute("UPDATE volunteers SET status = 'assigned' WHERE id = ?", (v["id"],))
                    cur.execute("""INSERT INTO assignment_history
                        (id, volunteer_id, help_request_id, assigned_by, instructions, assigned_at)
                        VALUES (?, ?, ?, ?, ?, ?)""",
                        (str(uuid.uuid4()), v["id"], req_id, ALGO_USER,
                         f"Deploy to {name} at {address}. Water depth {water}m.", verified_at + 300))
                    break

        dist = haversine_distance(lat, lon, chosen["lat"], chosen["lon"])
        alloc_ts = verified_at + 300
        rationale = {
            "timestamp": alloc_ts, "request_id": req_id, "priority_score": score,
            "score_breakdown": factors,
            "matched_shelter": {"id": chosen["id"], "name": chosen["name"],
                                "distance_km": dist, "beds_reserved": beds},
            "allocated_supplies": supplies, "assigned_volunteer": vol,
            "alternatives_evaluated": alternatives,
            "explanation_text": (
                f"Request from {name} (Priority Score {score}) was matched with {chosen['name']} "
                f"({dist:.2f} km away). Allocated {beds} shelter beds and {len(supplies)} supply item types. "
                + (f"Dispatched volunteer {vol['name']} with '{vol['skill_matched']}' skill."
                   if vol else "Shelter staging team notified for field dispatch.")),
        }
        alloc_id = f"ALC-2026-{seq:04d}"
        cur.execute("""
            INSERT INTO allocations (id, help_request_id, shelter_id, inventory_item_id, quantity_allocated,
                beds_allocated, status, allocated_by_algorithm, decision_rationale, allocated_at,
                dispatched_at, completed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (alloc_id, req_id, chosen["id"], supplies[0]["item_id"] if supplies else None,
             sum(x["quantity"] for x in supplies), beds,
             "delivered" if status == "resolved" else "reserved", 1, json.dumps(rationale), alloc_ts,
             alloc_ts + 1800 if status == "resolved" else None, resolved_at))

        _audit(conn, "allocation", alloc_id, "RESOURCES_ALLOCATED",
               {"request_id": req_id, "shelter": chosen["name"], "distance_km": dist,
                "beds_reserved": beds, "supplies": [s["item_name"] for s in supplies]},
               "Allocation Engine", alloc_ts)
        if status == "resolved":
            _audit(conn, "help_request", req_id, "REQUEST_RESOLVED",
                   {"resolution": notes, "shelter": chosen["name"]}, "Sarah Jenkins", resolved_at)
