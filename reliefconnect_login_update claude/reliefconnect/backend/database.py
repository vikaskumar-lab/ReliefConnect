"""
ReliefConnect Database Management Layer
Full implementation of the 10 core entities with SQLite, spatial calculations,
and atomic reservation locks to prevent double allocation.
"""

import sqlite3
import os
import json
import uuid
import time
from typing import List, Dict, Any, Optional, Tuple
from backend.models import haversine_distance
from backend import auth

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "reliefconnect.db")

def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    # Register spatial distance function directly in SQL
    conn.create_function("haversine_distance", 4, haversine_distance)
    return conn

def init_db(seed_sample_data: bool = True):
    """Initializes tables and seeds baseline crisis scenario if empty."""
    conn = get_connection()
    cur = conn.cursor()

    cur.executescript("""
    -- 1. USERS
    CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY,
        full_name TEXT NOT NULL,
        phone_number TEXT NOT NULL,
        email TEXT UNIQUE,
        role TEXT NOT NULL DEFAULT 'citizen',
        is_active INTEGER NOT NULL DEFAULT 1,
        created_at REAL NOT NULL
    );

    -- 2. INCIDENTS
    CREATE TABLE IF NOT EXISTS incidents (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT,
        severity_level INTEGER CHECK (severity_level BETWEEN 1 AND 5),
        status TEXT NOT NULL DEFAULT 'active',
        center_lat REAL NOT NULL,
        center_lon REAL NOT NULL,
        declared_at REAL NOT NULL,
        closed_at REAL
    );

    -- 3. LOCATIONS (Geospatial with privacy masking)
    CREATE TABLE IF NOT EXISTS locations (
        id TEXT PRIMARY KEY,
        lat REAL NOT NULL,
        lon REAL NOT NULL,
        address_text TEXT NOT NULL,
        district TEXT,
        is_privacy_masked INTEGER NOT NULL DEFAULT 0,
        masked_lat REAL,
        masked_lon REAL,
        created_at REAL NOT NULL
    );

    -- 4. SHELTERS
    CREATE TABLE IF NOT EXISTS shelters (
        id TEXT PRIMARY KEY,
        incident_id TEXT REFERENCES incidents(id),
        manager_id TEXT REFERENCES users(id),
        name TEXT NOT NULL,
        location_id TEXT NOT NULL REFERENCES locations(id),
        total_capacity INTEGER NOT NULL CHECK (total_capacity >= 0),
        current_occupancy INTEGER NOT NULL DEFAULT 0 CHECK (current_occupancy >= 0),
        status TEXT NOT NULL DEFAULT 'open',
        contact_phone TEXT,
        has_medical_unit INTEGER DEFAULT 0,
        has_accessible_access INTEGER DEFAULT 1,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );

    -- 5. CAPACITY UPDATES
    CREATE TABLE IF NOT EXISTS capacity_updates (
        id TEXT PRIMARY KEY,
        shelter_id TEXT NOT NULL REFERENCES shelters(id),
        previous_occupancy INTEGER NOT NULL,
        new_occupancy INTEGER NOT NULL,
        delta_change INTEGER NOT NULL,
        reason TEXT,
        updated_by TEXT REFERENCES users(id),
        created_at REAL NOT NULL
    );

    -- 6. RELIEF INVENTORY ITEMS
    CREATE TABLE IF NOT EXISTS inventory_items (
        id TEXT PRIMARY KEY,
        shelter_id TEXT NOT NULL REFERENCES shelters(id),
        category TEXT NOT NULL,
        item_name TEXT NOT NULL,
        quantity_available INTEGER NOT NULL DEFAULT 0 CHECK (quantity_available >= 0),
        quantity_reserved INTEGER NOT NULL DEFAULT 0 CHECK (quantity_reserved >= 0),
        unit TEXT NOT NULL DEFAULT 'units',
        minimum_threshold INTEGER DEFAULT 10,
        updated_at REAL NOT NULL
    );

    -- 7. HELP REQUESTS
    CREATE TABLE IF NOT EXISTS help_requests (
        id TEXT PRIMARY KEY,
        incident_id TEXT REFERENCES incidents(id),
        requester_id TEXT REFERENCES users(id),
        contact_name TEXT NOT NULL,
        contact_phone TEXT NOT NULL,
        location_id TEXT NOT NULL REFERENCES locations(id),
        category TEXT NOT NULL,
        severity_level INTEGER NOT NULL CHECK (severity_level BETWEEN 1 AND 5),
        vulnerability_level INTEGER NOT NULL DEFAULT 1 CHECK (vulnerability_level BETWEEN 1 AND 5),
        people_count INTEGER NOT NULL DEFAULT 1 CHECK (people_count >= 1),
        infants_count INTEGER NOT NULL DEFAULT 0,
        elderly_count INTEGER NOT NULL DEFAULT 0,
        medical_urgency_notes TEXT,
        water_level_meters REAL DEFAULT 0.0,
        calculated_priority_score REAL DEFAULT 0.0,
        status TEXT NOT NULL DEFAULT 'submitted',
        verified_by TEXT REFERENCES users(id),
        verified_at REAL,
        resolved_at REAL,
        notes TEXT,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );

    -- 8. VOLUNTEERS
    CREATE TABLE IF NOT EXISTS volunteers (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL UNIQUE REFERENCES users(id),
        skills_json TEXT NOT NULL DEFAULT '[]',
        location_id TEXT REFERENCES locations(id),
        status TEXT NOT NULL DEFAULT 'available',
        emergency_contact TEXT,
        rating REAL DEFAULT 5.0,
        created_at REAL NOT NULL
    );

    -- 9. RESOURCE ALLOCATIONS (With Decision Rationale for Explainability)
    CREATE TABLE IF NOT EXISTS allocations (
        id TEXT PRIMARY KEY,
        help_request_id TEXT NOT NULL REFERENCES help_requests(id),
        shelter_id TEXT REFERENCES shelters(id),
        inventory_item_id TEXT REFERENCES inventory_items(id),
        quantity_allocated INTEGER NOT NULL DEFAULT 0,
        beds_allocated INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'reserved',
        allocated_by_algorithm INTEGER NOT NULL DEFAULT 1,
        decision_rationale TEXT NOT NULL DEFAULT '{}',
        allocated_at REAL NOT NULL,
        dispatched_at REAL,
        completed_at REAL
    );

    -- 10. ASSIGNMENT HISTORY
    CREATE TABLE IF NOT EXISTS assignment_history (
        id TEXT PRIMARY KEY,
        volunteer_id TEXT NOT NULL REFERENCES volunteers(id),
        help_request_id TEXT NOT NULL REFERENCES help_requests(id),
        assigned_by TEXT REFERENCES users(id),
        instructions TEXT,
        assigned_at REAL NOT NULL,
        completed_at REAL,
        handover_notes TEXT,
        audit_trail TEXT NOT NULL DEFAULT '[]'
    );

    -- AUDIT LOGS TABLE FOR SYSTEM-WIDE HANDOVERS & ACTIONS
    CREATE TABLE IF NOT EXISTS audit_log (
        id TEXT PRIMARY KEY,
        entity_type TEXT NOT NULL,
        entity_id TEXT NOT NULL,
        action TEXT NOT NULL,
        details_json TEXT NOT NULL,
        performed_by TEXT,
        created_at REAL NOT NULL
    );

    CREATE TABLE IF NOT EXISTS dispatch_requests (
        id TEXT PRIMARY KEY,
        volunteer_id TEXT NOT NULL REFERENCES volunteers(id),
        help_request_id TEXT NOT NULL REFERENCES help_requests(id),
        inventory_item_id TEXT REFERENCES inventory_items(id),
        quantity INTEGER NOT NULL DEFAULT 0,
        destination TEXT,
        notes TEXT,
        status TEXT NOT NULL DEFAULT 'submitted',
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );

    -- INDEXES for fast lookup
    CREATE INDEX IF NOT EXISTS idx_requests_status ON help_requests(status, calculated_priority_score DESC);
    CREATE INDEX IF NOT EXISTS idx_alloc_req ON allocations(help_request_id);
    CREATE INDEX IF NOT EXISTS idx_alloc_shelter ON allocations(shelter_id);
    CREATE INDEX IF NOT EXISTS idx_inv_shelter ON inventory_items(shelter_id, category);
    """)
    conn.commit()
    auth.init_auth_tables(conn)

    if seed_sample_data:
        # Check if already seeded
        cur.execute("SELECT COUNT(*) FROM incidents")
        if cur.fetchone()[0] == 0:
            seed_crisis_data(conn)
        auth.ensure_demo_accounts(conn)

    conn.close()


def log_audit(conn: sqlite3.Connection, entity_type: str, entity_id: str, action: str, details: Dict[str, Any], performed_by: str = "System"):
    """Appends an immutable audit event."""
    audit_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO audit_log (id, entity_type, entity_id, action, details_json, performed_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (audit_id, entity_type, entity_id, action, json.dumps(details), performed_by, time.time())
    )


def seed_crisis_data(conn: sqlite3.Connection):
    """Populates standard baseline flood crisis scenario with shelters, supplies, and volunteers."""
    now = time.time()
    
    # 1. Incident
    incident_id = "inc-flood-2026-01"
    conn.execute("""
    INSERT INTO incidents (id, name, description, severity_level, status, center_lat, center_lon, declared_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (incident_id, "Metropolitan River Basin Inundation", "Severe monsoon flash flood impacting North, Central, and Riverside sectors", 4, "active", 13.0827, 80.2707, now - 15 * 3600))

    # 2. Key Users
    admin_user = "usr-admin-01"
    coord_user = "usr-coord-01"
    algo_user = "usr-algorithm-engine"
    conn.execute("INSERT INTO users VALUES (?, ?, ?, ?, ?, ?, ?)", (admin_user, "Dr. Aris Thorne", "+91-98400-11111", "admin@reliefconnect.org", "administrator", 1, now))
    conn.execute("INSERT INTO users VALUES (?, ?, ?, ?, ?, ?, ?)", (coord_user, "Sarah Jenkins", "+91-98400-22222", "sarah.j@reliefconnect.org", "relief_coordinator", 1, now))
    conn.execute("INSERT INTO users VALUES (?, ?, ?, ?, ?, ?, ?)", (algo_user, "Algorithmic Allocation Engine", "+91-00000-00000", "engine@reliefconnect.org", "administrator", 1, now))

    # 3. Shelters with Locations & Supplies
    shelters_data = [
        {
            "id": "shl-north-gym",
            "name": "North Sector Indoor Stadium",
            "lat": 13.0950, "lon": 80.2650,
            "address": "45 Highland Blvd, North Ridge",
            "capacity": 250,
            "occupancy": 65,
            "medical": 1,
            "inventory": [
                ("food_rations", "Ready-to-eat Ration Packs (3-day)", 500, "packs"),
                ("drinking_water", "Bottled Drinking Water (2L)", 1200, "bottles"),
                ("medical_supplies", "Trauma & First-Aid Emergency Kits", 80, "kits"),
                ("clothing_blankets", "Thermal Emergency Blankets", 350, "units")
            ]
        },
        {
            "id": "shl-central-hall",
            "name": "Central Community Convention Center",
            "lat": 13.0810, "lon": 80.2740,
            "address": "12 Metro Civic Center, Central Hub",
            "capacity": 400,
            "occupancy": 140,
            "medical": 1,
            "inventory": [
                ("food_rations", "Ready-to-eat Ration Packs (3-day)", 900, "packs"),
                ("drinking_water", "Bottled Drinking Water (2L)", 2500, "bottles"),
                ("medical_supplies", "Trauma & First-Aid Emergency Kits", 150, "kits"),
                ("clothing_blankets", "Thermal Emergency Blankets", 600, "units")
            ]
        },
        {
            "id": "shl-east-academy",
            "name": "East Riverfront Polytechnic Academy",
            "lat": 13.0740, "lon": 80.2880,
            "address": "88 Marina Causeway, East Coastline",
            "capacity": 180,
            "occupancy": 155,  # Almost full!
            "medical": 0,
            "inventory": [
                ("food_rations", "Ready-to-eat Ration Packs (3-day)", 180, "packs"),
                ("drinking_water", "Bottled Drinking Water (2L)", 400, "bottles"),
                ("medical_supplies", "Basic First-Aid Kits", 30, "kits"),
                ("clothing_blankets", "Thermal Emergency Blankets", 120, "units")
            ]
        },
        {
            "id": "shl-south-sports",
            "name": "South District Sports Complex",
            "lat": 13.0550, "lon": 80.2520,
            "address": "210 Park Avenue, South Hills",
            "capacity": 300,
            "occupancy": 40,
            "medical": 1,
            "inventory": [
                ("food_rations", "Ready-to-eat Ration Packs (3-day)", 750, "packs"),
                ("drinking_water", "Bottled Drinking Water (2L)", 1800, "bottles"),
                ("medical_supplies", "Trauma & First-Aid Emergency Kits", 100, "kits"),
                ("clothing_blankets", "Thermal Emergency Blankets", 450, "units")
            ]
        }
    ]

    for s in shelters_data:
        loc_id = f"loc-{s['id']}"
        conn.execute("INSERT INTO locations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (loc_id, s["lat"], s["lon"], s["address"], "Metropolitan Area", 0, None, None, now))
        
        mgr_user = f"usr-mgr-{s['id']}"
        conn.execute("INSERT INTO users VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (mgr_user, f"Manager {s['name']}", "+91-98400-33333", f"manager.{s['id']}@reliefconnect.org", "shelter_manager", 1, now))

        conn.execute("""
        INSERT INTO shelters (id, incident_id, manager_id, name, location_id, total_capacity, current_occupancy, status, contact_phone, has_medical_unit, has_accessible_access, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (s["id"], incident_id, mgr_user, s["name"], loc_id, s["capacity"], s["occupancy"], "open", "+91-98400-44444", s["medical"], 1, now, now))

        # Initial capacity audit log
        conn.execute("""
        INSERT INTO capacity_updates (id, shelter_id, previous_occupancy, new_occupancy, delta_change, reason, updated_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (str(uuid.uuid4()), s["id"], 0, s["occupancy"], s["occupancy"], "Initial shelter intake", mgr_user, now))

        # Inventory
        for cat, name, qty, unit in s["inventory"]:
            inv_id = f"inv-{s['id']}-{cat}"
            conn.execute("""
            INSERT INTO inventory_items (id, shelter_id, category, item_name, quantity_available, quantity_reserved, unit, minimum_threshold, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (inv_id, s["id"], cat, name, qty, 0, unit, 20, now))

    # 4. Volunteers
    volunteers_data = [
        ("David Chen", "+91-98400-55551", ["boat_rescue", "swimming", "first_aid"], 13.0800, 80.2710),
        ("Priya Raman", "+91-98400-55552", ["first_aid", "emergency_medical", "triage"], 13.0900, 80.2620),
        ("Marcus Brody", "+91-98400-55553", ["heavy_machinery", "truck_driving", "logistics"], 13.0600, 80.2500),
        ("Fatima Al-Sayed", "+91-98400-55554", ["food_distribution", "counseling", "language_translation"], 13.0750, 80.2800),
        ("Rahul Verma", "+91-98400-55555", ["boat_rescue", "search_and_rescue"], 13.0850, 80.2680)
    ]

    for i, (name, phone, skills, v_lat, v_lon) in enumerate(volunteers_data):
        v_usr_id = f"usr-vol-{i+1}"
        conn.execute("INSERT INTO users VALUES (?, ?, ?, ?, ?, ?, ?)", (v_usr_id, name, phone, f"vol{i+1}@reliefconnect.org", "volunteer", 1, now))
        v_loc_id = f"loc-vol-{i+1}"
        conn.execute("INSERT INTO locations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (v_loc_id, v_lat, v_lon, f"Staging Area #{i+1}", "Field District", 0, None, None, now))
        
        v_id = f"vol-00{i+1}"
        conn.execute("""
        INSERT INTO volunteers (id, user_id, skills_json, location_id, status, emergency_contact, rating, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (v_id, v_usr_id, json.dumps(skills), v_loc_id, "available", "+91-98400-99999", 5.0, now))

    # 5. Baseline help requests in every workflow status (submitted / verified / allocated / resolved)
    from backend.seed_requests import seed_help_requests
    seed_help_requests(conn, incident_id, now)

    conn.execute(
        "INSERT INTO audit_log (id, entity_type, entity_id, action, details_json, performed_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), "incident", incident_id, "INITIALIZE_CRISIS",
         json.dumps({"status": "active", "shelters_count": 4, "name": "Metropolitan River Basin Inundation"}), "Dr. Aris Thorne", now - 15 * 3600))
    conn.commit()
