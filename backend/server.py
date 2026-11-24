"""
ReliefConnect REST API & Web Server
Multi-threaded, robust HTTP server using Python standard library.
Serves full REST endpoints for Citizens, Coordinators, Shelters, Volunteers, Simulation & Frontend.
"""

import http.server
import json
import os
import sys
import time
import urllib.parse
import uuid
import sqlite3
from typing import Dict, Any, Optional

from backend.database import get_connection, init_db, log_audit, seed_crisis_data, DB_PATH
from backend import auth
from backend.algorithm import AllocationEngine
from backend.simulator import FloodSimulator
from backend.models import haversine_distance

FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")

class ReliefConnectRequestHandler(http.server.SimpleHTTPRequestHandler):
    """Custom HTTP handler dispatching REST API routes and serving static UI files."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=FRONTEND_DIR, **kwargs)

    def _send_json(
        self,
        data: Any,
        status: int = 200,
        extra_headers: Optional[Dict[str, str]] = None
    ):
        response_bytes = json.dumps(data, indent=2).encode("utf-8")

        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(response_bytes)))

        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header(
            "Access-Control-Allow-Methods",
            "GET, POST, PUT, DELETE, OPTIONS"
        )
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

        if extra_headers:
            for header_name, header_value in extra_headers.items():
                self.send_header(header_name, header_value)

        self.end_headers()
        self.wfile.write(response_bytes)

    def _read_json_body(self) -> Dict[str, Any]:
        content_len = int(self.headers.get("Content-Length", 0))
        if content_len == 0:
            return {}
        body = self.rfile.read(content_len).decode("utf-8")
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {}

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path.startswith("/api/"):
            self._handle_api_get(path, query)
        else:
            # Fallback to serving frontend files (index.html, styles.css, app.js)
            if path == "/" or path == "":
                self.path = "/index.html"
            super().do_GET()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path.startswith("/api/auth/"):
           self._handle_auth_post(path)

        elif path.startswith("/api/"):
           self._handle_api_post(path)

        else:
           self._send_json({"error": "Not Found"}, 404)

    def do_PUT(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path.startswith("/api/"):
            self._handle_api_put(path)
        else:
            self._send_json({"error": "Not Found"}, 404)
    
    # =========================================================================
    # AUTHENTICATION HANDLERS
    # =========================================================================

    def _handle_auth_post(self, path: str):
        body = self._read_json_body()
        conn = get_connection()

        try:
            # 1. User Registration
            if path == "/api/auth/register":
                full_name = str(body.get("full_name", "")).strip()
                phone = str(body.get("phone", "")).strip()
                email = str(body.get("email", "")).strip().lower()
                password = body.get("password", "")
                role = str(body.get("role", "")).strip().lower()

                if not full_name or not phone or not email:
                    self._send_json(
                        {"error": "Name, phone and email are required."}, 400
                    )
                    return

                if "@" not in email or "." not in email.split("@")[-1]:
                    self._send_json({"error": "Enter a valid email address."}, 400)
                    return

                if not isinstance(password, str) or len(password) < auth.MIN_PASSWORD_LEN:
                    self._send_json(
                        {"error": "Password must contain at least 8 characters."}, 400
                    )
                    return

                if role not in auth.PUBLIC_REGISTRATION_ROLES:
                    self._send_json(
                        {"error": "This role cannot be registered publicly."}, 403
                    )
                    return

                existing = conn.execute(
                    "SELECT id FROM users WHERE email = ?", (email,)
                ).fetchone()

                if existing:
                    self._send_json({"error": "Email is already registered."}, 409)
                    return

                user_id = f"usr-{str(uuid.uuid4())[:8]}"
                now = time.time()

                # Staff accounts require Administrator approval.
                is_active = 1 if role in (
                    auth.CITIZEN, auth.VOLUNTEER
                ) else 0

                conn.execute("""
                    INSERT INTO users (
                        id, full_name, phone_number, email,
                        role, is_active, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    user_id, full_name, phone, email,
                    role, is_active, now
                ))

                auth.save_password(conn, user_id, password)

                if role == auth.VOLUNTEER:
                    conn.execute("""
                        INSERT INTO volunteers (
                            id, user_id, skills_json, location_id,
                            status, emergency_contact, rating, created_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        f"vol-{str(uuid.uuid4())[:8]}",
                        user_id,
                        json.dumps(["first_aid"]),
                        None,
                        "available",
                        phone,
                        5.0,
                        now
                    ))

                conn.commit()

                if not is_active:
                    self._send_json({
                        "status": "pending_approval",
                        "message": "Registration successful. Wait for Administrator approval."
                    }, 201)
                    return

                token = auth.create_session(conn, user_id)

                self._send_json({
                    "status": "registered",
                    "message": "Account created successfully.",
                    "user": {
                        "id": user_id,
                        "full_name": full_name,
                        "email": email,
                        "role": role
                    }
                }, 201, {
                    "Set-Cookie": auth.session_cookie_header(token)
                })
                return

            # 2. User Login
            elif path == "/api/auth/login":
                email = str(body.get("email", "")).strip().lower()
                password = body.get("password", "")

                if not email or not isinstance(password, str):
                    self._send_json(
                        {"error": "Email and password are required."}, 400
                    )
                    return

                user = conn.execute("""
                    SELECT users.id, users.full_name, users.email,
                           users.role, users.is_active,
                           credentials.password_hash
                    FROM users
                    LEFT JOIN credentials
                        ON users.id = credentials.user_id
                    WHERE users.email = ?
                """, (email,)).fetchone()

                if not user or not user["password_hash"]:
                    self._send_json(
                        {"error": "Invalid email or password."}, 401
                    )
                    return

                if not auth.verify_password(
                    password, user["password_hash"]
                ):
                    self._send_json(
                        {"error": "Invalid email or password."}, 401
                    )
                    return

                if not user["is_active"]:
                    self._send_json({
                        "error": "Your account is awaiting Administrator approval."
                    }, 403)
                    return

                token = auth.create_session(conn, user["id"])

                self._send_json({
                    "status": "success",
                    "message": "Login successful.",
                    "user": {
                        "id": user["id"],
                        "full_name": user["full_name"],
                        "email": user["email"],
                        "role": user["role"]
                    }
                }, 200, {
                    "Set-Cookie": auth.session_cookie_header(token)
                })
                return

            # 3. User Logout
            elif path == "/api/auth/logout":
                cookie_header = self.headers.get("Cookie", "")
                token = auth.get_token_from_cookie(cookie_header)

                if token:
                    auth.delete_session(conn, token)

                self._send_json(
                    {"status": "success", "message": "Logged out successfully."},
                    200,
                    {"Set-Cookie": auth.clear_session_cookie_header()}
                )
                return

            else:
                self._send_json({"error": "Authentication route not found."}, 404)

        except sqlite3.IntegrityError:
            conn.rollback()
            self._send_json({"error": "Account already exists or registration data is invalid."}, 409)

        except Exception:
            conn.rollback()
            self._send_json({"error": "Authentication request failed."}, 500)

        finally:
            conn.close()


    # =========================================================================
    # GET HANDLERS
    # =========================================================================
    
    def _handle_api_get(self, path: str, query: Dict[str, list]):
        # Check the current login session
        if path == "/api/auth/me":
            cookie_header = self.headers.get("Cookie", "")
            token = auth.get_token_from_cookie(cookie_header)

            if not token:
                self._send_json({"error": "Not authenticated"}, 401)
                return

            conn = get_connection()
            try:
                user = auth.get_user_by_token(conn, token)

                if not user:
                    self._send_json({"error": "Session expired or invalid"}, 401)
                    return

                self._send_json({
                    "status": "authenticated",
                    "user": {
                        "id": user["id"],
                        "full_name": user["full_name"],
                        "email": user["email"],
                        "role": user["role"]
                    }
                })
                return
            finally:
                conn.close()

        conn = get_connection()
        cur = conn.cursor()
        try:
            # 1. Incident Info & System Stats
            if path == "/api/dashboard/stats":
                cur.execute("SELECT * FROM incidents WHERE status = 'active' LIMIT 1")
                inc_row = cur.fetchone()
                incident = dict(inc_row) if inc_row else None

                cur.execute("""
                SELECT
                    COUNT(*) as total_requests,
                    SUM(CASE WHEN status = 'submitted' THEN 1 ELSE 0 END) as submitted_count,
                    SUM(CASE WHEN status = 'verified' THEN 1 ELSE 0 END) as verified_count,
                    SUM(CASE WHEN status = 'allocated' THEN 1 ELSE 0 END) as allocated_count,
                    SUM(CASE WHEN status = 'in_progress' THEN 1 ELSE 0 END) as in_progress_count,
                    SUM(CASE WHEN status = 'resolved' THEN 1 ELSE 0 END) as resolved_count,
                    SUM(people_count) as total_people_impacted
                FROM help_requests
                """)
                req_stats = dict(cur.fetchone())

                cur.execute("""
                SELECT
                    COUNT(*) as shelter_count,
                    SUM(total_capacity) as total_bed_capacity,
                    SUM(current_occupancy) as total_current_occupancy
                FROM shelters
                """)
                shelter_stats = dict(cur.fetchone())

                cur.execute("""
                SELECT
                    COUNT(*) as total_volunteers,
                    SUM(CASE WHEN status = 'available' THEN 1 ELSE 0 END) as available_volunteers,
                    SUM(CASE WHEN status = 'assigned' THEN 1 ELSE 0 END) as assigned_volunteers
                FROM volunteers
                """)
                vol_stats = dict(cur.fetchone())

                cur.execute("""
                SELECT category, SUM(quantity_available) as total_available, SUM(quantity_reserved) as total_reserved, unit
                FROM inventory_items
                GROUP BY category, unit
                """)
                inv_summary = [dict(r) for r in cur.fetchall()]

                self._send_json({
                    "incident": incident,
                    "requests": req_stats,
                    "shelters": shelter_stats,
                    "volunteers": vol_stats,
                    "inventory": inv_summary,
                    "timestamp": time.time()
                })

            # 2. Help Requests list
            elif path == "/api/requests":
                status_filter = query.get("status", [None])[0]
                sql = """
                SELECT r.*, l.lat, l.lon, l.address_text, l.district, l.is_privacy_masked, l.masked_lat, l.masked_lon,
                       a.id as allocation_id, a.shelter_id as allocated_shelter_id, a.decision_rationale,
                       s.name as allocated_shelter_name
                FROM help_requests r
                JOIN locations l ON r.location_id = l.id
                LEFT JOIN allocations a ON r.id = a.help_request_id AND a.status != 'cancelled'
                LEFT JOIN shelters s ON a.shelter_id = s.id
                """
                params = []
                if status_filter:
                    sql += " WHERE r.status = ?"
                    params.append(status_filter)
                sql += " ORDER BY r.calculated_priority_score DESC, r.created_at ASC"

                cur.execute(sql, params)
                rows = cur.fetchall()
                results = []
                for r in rows:
                    d = dict(r)
                    if d.get("decision_rationale"):
                        try:
                            d["decision_rationale"] = json.loads(d["decision_rationale"])
                        except Exception:
                            pass
                    # If privacy masked, adjust display lat/lon
                    if d["is_privacy_masked"] and d["masked_lat"]:
                        d["display_lat"] = d["masked_lat"]
                        d["display_lon"] = d["masked_lon"]
                        d["address_text"] = f"[Privacy Masked: ~500m area] {d['district'] or 'Affected Zone'}"
                    else:
                        d["display_lat"] = d["lat"]
                        d["display_lon"] = d["lon"]
                    results.append(d)
                self._send_json(results)

            # 3. Shelters list
            elif path == "/api/shelters":
                cur.execute("""
                SELECT s.*, l.lat, l.lon, l.address_text, l.district
                FROM shelters s
                JOIN locations l ON s.location_id = l.id
                ORDER BY s.name ASC
                """)
                shelters = [dict(r) for r in cur.fetchall()]
                # fetch inventory for each
                for s in shelters:
                    cur.execute("""
                    SELECT id, category, item_name, quantity_available, quantity_reserved, unit
                    FROM inventory_items
                    WHERE shelter_id = ?
                    """, (s["id"],))
                    s["inventory"] = [dict(i) for i in cur.fetchall()]
                self._send_json(shelters)

            # 4. Inventory list
            elif path == "/api/inventory":
                cur.execute("""
                SELECT i.*, s.name as shelter_name
                FROM inventory_items i
                JOIN shelters s ON i.shelter_id = s.id
                ORDER BY i.category ASC, s.name ASC
                """)
                self._send_json([dict(r) for r in cur.fetchall()])

            # 5. Volunteers list
            elif path == "/api/volunteers":
                cur.execute("""
                SELECT v.*, u.full_name, u.phone_number, u.email, l.lat, l.lon, l.address_text
                FROM volunteers v
                JOIN users u ON v.user_id = u.id
                LEFT JOIN locations l ON v.location_id = l.id
                ORDER BY v.status ASC, u.full_name ASC
                """)
                rows = cur.fetchall()
                vols = []
                for r in rows:
                    d = dict(r)
                    d["skills"] = json.loads(d["skills_json"]) if d.get("skills_json") else []
                    vols.append(d)
                self._send_json(vols)

            # 6. Allocations & Decision Rationales
            elif path == "/api/allocations":
                cur.execute("""
                SELECT a.*, r.contact_name, r.category, r.people_count, r.severity_level,
                       s.name as shelter_name
                FROM allocations a
                JOIN help_requests r ON a.help_request_id = r.id
                LEFT JOIN shelters s ON a.shelter_id = s.id
                ORDER BY a.allocated_at DESC
                """)
                rows = cur.fetchall()
                results = []
                for r in rows:
                    d = dict(r)
                    if d.get("decision_rationale"):
                        try:
                            d["decision_rationale"] = json.loads(d["decision_rationale"])
                        except Exception:
                            pass
                    results.append(d)
                self._send_json(results)

            # 7. Incident Timeline & Audit Trail
            elif path == "/api/audit-trail":
                cur.execute("SELECT * FROM audit_log ORDER BY created_at DESC LIMIT 300")
                logs = []
                for r in cur.fetchall():
                    d = dict(r)
                    if d.get("details_json"):
                        try:
                            d["details"] = json.loads(d["details_json"])
                        except Exception:
                            pass
                    logs.append(d)
                self._send_json(logs)

            # 8. Capacity update history for shelters
            elif path == "/api/shelters/capacity-updates":
                cur.execute("""
                SELECT cu.*, s.name as shelter_name
                FROM capacity_updates cu
                JOIN shelters s ON cu.shelter_id = s.id
                ORDER BY cu.created_at DESC
                LIMIT 50
                """)
                self._send_json([dict(r) for r in cur.fetchall()])

            else:
                self._send_json({"error": "Route not found"}, 404)
        finally:
            conn.close()

    # =========================================================================
    # POST HANDLERS
    # =========================================================================
    def _handle_api_post(self, path: str):
        body = self._read_json_body()
        conn = get_connection()
        cur = conn.cursor()
        now = time.time()
        try:
            # 1. Citizen Help Request Submission
            if path == "/api/requests":
                contact_name = body.get("contact_name", "Anonymous Citizen").strip()
                contact_phone = body.get("contact_phone", "").strip()
                address_text = body.get("address_text", "Current Location").strip()
                lat = float(body.get("lat", 13.0827))
                lon = float(body.get("lon", 80.2707))
                category = body.get("category", "rescue_evacuation")
                severity = int(body.get("severity_level", 3))
                vulnerability = int(body.get("vulnerability_level", 2))
                people_count = int(body.get("people_count", 1))
                infants_count = int(body.get("infants_count", 0))
                elderly_count = int(body.get("elderly_count", 0))
                medical_notes = body.get("medical_urgency_notes", "")
                water_level = float(body.get("water_level_meters", 0.0))
                is_privacy_masked = 1 if body.get("is_privacy_masked", False) else 0

                cur.execute("SELECT COUNT(*) FROM help_requests WHERE id LIKE 'REQ-2026-%'")
                req_id = f"REQ-2026-{cur.fetchone()[0] + 1:04d}"
                while cur.execute("SELECT 1 FROM help_requests WHERE id = ?", (req_id,)).fetchone():
                    req_id = f"REQ-2026-{int(req_id[-4:]) + 1:04d}"
                loc_id = f"loc-{req_id}"

                masked_lat = lat + 0.004 if is_privacy_masked else None
                masked_lon = lon + 0.004 if is_privacy_masked else None

                # Insert Location
                cur.execute("""
                INSERT INTO locations (id, lat, lon, address_text, district, is_privacy_masked, masked_lat, masked_lon, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (loc_id, lat, lon, address_text, "Affected Sector", is_privacy_masked, masked_lat, masked_lon, now))

                # Calculate score
                score, breakdown = AllocationEngine.calculate_priority_score(severity, vulnerability, water_level, now, now)

                # Check incident
                cur.execute("SELECT id FROM incidents WHERE status = 'active' LIMIT 1")
                inc_row = cur.fetchone()
                incident_id = inc_row[0] if inc_row else None

                cur.execute("""
                INSERT INTO help_requests (
                    id, incident_id, contact_name, contact_phone, location_id,
                    category, severity_level, vulnerability_level, people_count, infants_count,
                    elderly_count, medical_urgency_notes, water_level_meters, calculated_priority_score,
                    status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    req_id, incident_id, contact_name, contact_phone, loc_id,
                    category, severity, vulnerability, people_count, infants_count,
                    elderly_count, medical_notes, water_level, score,
                    "submitted", now, now
                ))

                log_audit(conn, "help_request", req_id, "REQUEST_SUBMITTED", {
                    "contact_name": contact_name,
                    "category": category,
                    "severity": severity,
                    "priority_score": score
                }, "Citizen")

                conn.commit()
                self._send_json({"status": "created", "request_id": req_id, "priority_score": score, "score_factors": breakdown}, 201)

            # 2. Coordinator Verification
            elif path.startswith("/api/requests/") and path.endswith("/verify"):
                req_id = path.split("/")[3]
                action = body.get("action", "verified") # "verified" or "rejected"
                notes = body.get("notes", "")
                coordinator_name = body.get("coordinator_name", "Coordinator")

                cur.execute("SELECT * FROM help_requests WHERE id = ?", (req_id,))
                req = cur.fetchone()
                if not req:
                    self._send_json({"error": "Request not found"}, 404)
                    return

                new_status = "verified" if action == "verified" else "rejected"
                cur.execute("UPDATE help_requests SET status = ?, notes = ?, updated_at = ? WHERE id = ?",
                            (new_status, notes, now, req_id))
                
                log_audit(conn, "help_request", req_id, "REQUEST_VERIFIED" if new_status == "verified" else "REQUEST_REJECTED", {
                    "action": action, "notes": notes
                }, coordinator_name)

                conn.commit()
                self._send_json({"status": "updated", "request_id": req_id, "new_status": new_status})

            # 3. Trigger Allocation Engine (Run on pending requests)
            elif path == "/api/algorithm/run-allocations":
                result = AllocationEngine.run_batch_allocation(conn)
                log_audit(conn, "algorithm", "engine", "BATCH_ALLOCATION_RUN", {
                    "allocated_count": result["allocated_count"],
                    "unresolved_count": result["unresolved_count"]
                }, "Coordinator")
                conn.commit()
                self._send_json(result)

            # 4. Trigger 100+ Flood Simulation Scenario
            elif path == "/api/simulation/run-100":
                # Clear non-initial requests or generate 120 synthetic ones
                gen_result = FloodSimulator.generate_flood_scenario(conn, num_requests=120)
                # Auto-run allocation algorithm across the scenario
                alloc_result = AllocationEngine.run_batch_allocation(conn)
                # Run validation verification
                val_result = FloodSimulator.validate_simulation_results(conn)

                log_audit(conn, "simulation", "sim-flood-100", "FLOOD_SCENARIO_EXECUTED", {
                    "generated": gen_result["total_generated"],
                    "allocated": alloc_result["allocated_count"],
                    "verdict": val_result["overall_status"]
                }, "System")
                conn.commit()

                self._send_json({
                    "simulation_generation": gen_result,
                    "allocation_results": alloc_result,
                    "validation_report": val_result
                })

            # 5. Dynamic Condition Failure (Shelter Floods / Fails)
            elif path == "/api/simulation/shelter-failure":
                shelter_id = body.get("shelter_id", "shl-east-academy")
                reason = body.get("reason", "Rapid floodwater breach into ground floor generator room")
                fail_result = AllocationEngine.simulate_shelter_failure(conn, shelter_id, reason)
                log_audit(conn, "shelter", shelter_id, "DYNAMIC_FAILURE_SIMULATED", fail_result, "SimulationEngine")
                conn.commit()
                self._send_json(fail_result)

            # 6. Reset Database to Baseline Crisis
            elif path == "/api/simulation/reset":
                if os.path.exists(DB_PATH):
                    conn.close()
                    try:
                        os.remove(DB_PATH)
                    except Exception:
                        pass
                init_db(seed_sample_data=True)
                self._send_json({"status": "reset_successful", "message": "Database reset to baseline active crisis."})

            # 7. Volunteer Registration
            elif path == "/api/volunteers":
                full_name = body.get("full_name", "").strip()
                phone = body.get("phone", "").strip()
                email = body.get("email", "").strip()
                skills = body.get("skills", ["first_aid"])
                lat = float(body.get("lat", 13.0800))
                lon = float(body.get("lon", 80.2700))

                usr_id = f"usr-{str(uuid.uuid4())[:8]}"
                loc_id = f"loc-{usr_id}"
                vol_id = f"vol-{str(uuid.uuid4())[:8]}"

                cur.execute("INSERT INTO users VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (usr_id, full_name, phone, email, "volunteer", 1, now))
                cur.execute("INSERT INTO locations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (loc_id, lat, lon, "Volunteer Staging Post", "Central Area", 0, None, None, now))
                cur.execute("""
                INSERT INTO volunteers (id, user_id, skills_json, location_id, status, emergency_contact, rating, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (vol_id, usr_id, json.dumps(skills), loc_id, "available", phone, 5.0, now))

                log_audit(conn, "volunteer", vol_id, "VOLUNTEER_REGISTERED", {"name": full_name, "skills": skills}, full_name)
                conn.commit()
                self._send_json({"status": "created", "volunteer_id": vol_id}, 201)

            # 8. Add Shelter
            elif path == "/api/shelters":
                name = body.get("name", "New Shelter").strip()
                lat = float(body.get("lat", 13.0800))
                lon = float(body.get("lon", 80.2700))
                address = body.get("address", "").strip()
                capacity = int(body.get("capacity", 200))
                medical = 1 if body.get("has_medical_unit", False) else 0

                shl_id = f"shl-{str(uuid.uuid4())[:8]}"
                loc_id = f"loc-{shl_id}"

                cur.execute("INSERT INTO locations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (loc_id, lat, lon, address, "District Area", 0, None, None, now))
                cur.execute("""
                INSERT INTO shelters (id, incident_id, manager_id, name, location_id, total_capacity, current_occupancy, status, contact_phone, has_medical_unit, has_accessible_access, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (shl_id, "inc-flood-2026-01", None, name, loc_id, capacity, 0, "open", "+91-98400-00000", medical, 1, now, now))

                # Add baseline inventory
                for cat, item, qty, unit in [
                    ("food_rations", "Ready-to-eat Ration Packs", 300, "packs"),
                    ("drinking_water", "Bottled Water (2L)", 800, "bottles"),
                    ("medical_supplies", "First-Aid Emergency Kits", 50, "kits"),
                    ("clothing_blankets", "Emergency Blankets", 200, "units")
                ]:
                    cur.execute("""
                    INSERT INTO inventory_items (id, shelter_id, category, item_name, quantity_available, quantity_reserved, unit, minimum_threshold, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (f"inv-{shl_id}-{cat}", shl_id, cat, item, qty, 0, unit, 10, now))

                log_audit(conn, "shelter", shl_id, "SHELTER_CREATED", {"name": name, "capacity": capacity}, "Admin")
                conn.commit()
                self._send_json({"status": "created", "shelter_id": shl_id}, 201)

            else:
                self._send_json({"error": "Route not found"}, 404)
        finally:
            conn.close()

    # =========================================================================
    # PUT HANDLERS
    # =========================================================================
    def _handle_api_put(self, path: str):
        body = self._read_json_body()
        conn = get_connection()
        cur = conn.cursor()
        now = time.time()
        try:
            # Shelter Capacity Update (by shelter manager)
            if path.startswith("/api/shelters/") and path.endswith("/capacity"):
                shelter_id = path.split("/")[3]
                new_occupancy = int(body.get("current_occupancy", 0))
                reason = body.get("reason", "Manual shelter intake / check-out update")
                manager_name = body.get("manager_name", "Shelter Manager")

                cur.execute("SELECT total_capacity, current_occupancy FROM shelters WHERE id = ?", (shelter_id,))
                shl = cur.fetchone()
                if not shl:
                    self._send_json({"error": "Shelter not found"}, 404)
                    return

                if new_occupancy < 0 or new_occupancy > shl["total_capacity"]:
                    self._send_json({"error": f"Invalid occupancy: must be between 0 and {shl['total_capacity']}"}, 400)
                    return

                prev_occ = shl["current_occupancy"]
                delta = new_occupancy - prev_occ

                status = "at_capacity" if new_occupancy >= shl["total_capacity"] else "open"
                cur.execute("UPDATE shelters SET current_occupancy = ?, status = ?, updated_at = ? WHERE id = ?",
                            (new_occupancy, status, now, shelter_id))

                cur.execute("""
                INSERT INTO capacity_updates (id, shelter_id, previous_occupancy, new_occupancy, delta_change, reason, updated_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (str(uuid.uuid4()), shelter_id, prev_occ, new_occupancy, delta, reason, manager_name, now))

                log_audit(conn, "shelter", shelter_id, "CAPACITY_UPDATED", {
                    "previous": prev_occ, "new": new_occupancy, "delta": delta, "reason": reason
                }, manager_name)

                conn.commit()
                self._send_json({"status": "updated", "shelter_id": shelter_id, "current_occupancy": new_occupancy, "shelter_status": status})

            # Inventory Restock
            elif path.startswith("/api/inventory/") and path.endswith("/restock"):
                item_id = path.split("/")[3]
                additional_qty = int(body.get("quantity", 0))
                if additional_qty <= 0:
                    self._send_json({"error": "Quantity must be > 0"}, 400)
                    return

                cur.execute("UPDATE inventory_items SET quantity_available = quantity_available + ?, updated_at = ? WHERE id = ?",
                            (additional_qty, now, item_id))
                log_audit(conn, "inventory", item_id, "RESTOCK", {"added": additional_qty}, "Logistics Team")
                conn.commit()
                self._send_json({"status": "restocked", "item_id": item_id})

            else:
                self._send_json({"error": "Route not found"}, 404)
        finally:
            conn.close()


def run_server(port: int = 8080):
    init_db(seed_sample_data=True)

    conn = get_connection()
    try:
        auth.init_auth_tables(conn)
    finally:
        conn.close()
    server_address = ("0.0.0.0", port)
    httpd = http.server.ThreadingHTTPServer(server_address, ReliefConnectRequestHandler)
    print(f"[ReliefConnect Server] Listening on http://localhost:{port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[ReliefConnect Server] Shutting down.")
        httpd.server_close()

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    run_server(port)
