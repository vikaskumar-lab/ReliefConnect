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
from backend.algorithm import AllocationEngine
from backend.simulator import FloodSimulator
from backend.models import haversine_distance
from backend import auth

FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")

class ReliefConnectRequestHandler(http.server.SimpleHTTPRequestHandler):
    """Custom HTTP handler dispatching REST API routes and serving static UI files."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=FRONTEND_DIR, **kwargs)

    def _send_json(self, data: Any, status: int = 200, headers: Optional[Dict[str, str]] = None):
        response_bytes = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(response_bytes)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "no-store")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(response_bytes)

    def _read_json_body(self) -> Dict[str, Any]:
        content_len = int(self.headers.get("Content-Length", 0))
        if content_len == 0 or content_len > 1_000_000:
            return {}
        body = self.rfile.read(content_len).decode("utf-8")
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {}

    # =========================================================================
    # AUTHENTICATION HELPERS
    # =========================================================================
    def _secure_request(self) -> bool:
        return self.headers.get("X-Forwarded-Proto", "").lower() == "https"

    def _token(self) -> Optional[str]:
        return auth.token_from_cookie_header(self.headers.get("Cookie"))

    def _authenticate(self, conn=None) -> Optional[Dict[str, Any]]:
        own = conn is None
        conn = conn or get_connection()
        try:
            return auth.user_for_token(conn, self._token())
        finally:
            if own:
                conn.close()

    def _authorize(self, conn, method: str, path: str) -> bool:
        """Checks sign-in and role. Sends 401/403 and returns False when refused."""
        self._user = self._authenticate(conn)
        if not self._user:
            self._send_json({"error": "Please sign in to continue."}, 401)
            return False
        allowed = auth.required_roles(method, path)
        if allowed is not None and self._user["role"] not in allowed:
            self._send_json({"error": "Your role does not have permission for this action."}, 403)
            return False
        return True

    def _redirect(self, location: str):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _client_key(self, email: str) -> str:
        forwarded = self.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        return f"{forwarded or self.client_address[0]}|{email}"

    def _handle_auth_post(self, conn, path: str, body: Dict[str, Any]):
        now = time.time()
        secure = self._secure_request()

        if path == "/api/auth/login":
            email = str(body.get("email", "")).strip().lower()
            password = str(body.get("password", ""))
            key = self._client_key(email)
            if auth.too_many_failures(key):
                self._send_json({"error": "Too many failed attempts. Please wait a few minutes and try again."}, 429)
                return
            row = conn.execute(
                "SELECT u.id, c.password_hash FROM users u LEFT JOIN credentials c ON c.user_id = u.id "
                "WHERE lower(u.email) = ? AND u.is_active = 1", (email,)).fetchone()
            if not row or not auth.verify_password(password, row["password_hash"]):
                auth.record_failure(key)
                self._send_json({"error": "Incorrect email or password."}, 401)
                return
            auth.clear_failures(key)
            token = auth.create_session(conn, row["id"])
            user = auth.user_for_token(conn, token)
            self._send_json({"user": user}, 200, {"Set-Cookie": auth.session_cookie(token, secure)})

        elif path == "/api/auth/phone/send-otp":
            phone = str(body.get("phone", "")).strip()
            row = conn.execute(
                "SELECT id FROM users WHERE replace(replace(replace(phone_number, ' ', ''), '-', ''), '+', '') "
                "= replace(replace(replace(?, ' ', ''), '-', ''), '+', '') AND is_active = 1",
                (phone,),
            ).fetchone()
            if not row:
                self._send_json({"error": "No account is registered with this phone number."}, 404)
                return
            code = auth.create_auth_code(conn, row["id"], "phone_login")
            print(f"[ReliefConnect Demo OTP] {phone}: {code}")
            self._send_json({"status": "otp_sent", "demo_otp": code})

        elif path == "/api/auth/phone/login":
            phone = str(body.get("phone", "")).strip()
            code = str(body.get("otp", "")).strip()
            row = conn.execute(
                "SELECT id FROM users WHERE replace(replace(replace(phone_number, ' ', ''), '-', ''), '+', '') "
                "= replace(replace(replace(?, ' ', ''), '-', ''), '+', '') AND is_active = 1",
                (phone,),
            ).fetchone()
            if not row or not auth.verify_auth_code(conn, row["id"], code, "phone_login"):
                self._send_json({"error": "Invalid or expired OTP."}, 401)
                return
            auth.consume_auth_code(conn, row["id"], "phone_login")
            token = auth.create_session(conn, row["id"])
            user = auth.user_for_token(conn, token)
            self._send_json({"user": user}, 200, {"Set-Cookie": auth.session_cookie(token, secure)})

        elif path == "/api/auth/forgot-password":
            email = str(body.get("email", "")).strip().lower()
            row = conn.execute(
                "SELECT id FROM users WHERE lower(email) = ? AND is_active = 1", (email,)
            ).fetchone()
            if row:
                code = auth.create_auth_code(conn, row["id"], "password_reset")
                print(f"[ReliefConnect Password Reset] {email}: {code}")
                self._send_json({"status": "reset_code_sent", "demo_reset_code": code})
            else:
                # Avoid revealing whether an account exists.
                self._send_json({"status": "reset_code_sent"})

        elif path == "/api/auth/reset-password":
            email = str(body.get("email", "")).strip().lower()
            code = str(body.get("code", "")).strip()
            password = str(body.get("password", ""))
            if len(password) < auth.MIN_PASSWORD_LEN:
                self._send_json({"error": f"Password must be at least {auth.MIN_PASSWORD_LEN} characters."}, 400)
                return
            row = conn.execute(
                "SELECT id FROM users WHERE lower(email) = ? AND is_active = 1", (email,)
            ).fetchone()
            if not row or not auth.verify_auth_code(conn, row["id"], code, "password_reset"):
                self._send_json({"error": "Invalid or expired reset code."}, 400)
                return
            auth.update_password(conn, row["id"], password)
            auth.consume_auth_code(conn, row["id"], "password_reset")
            self._send_json({"status": "password_reset"})

        elif path == "/api/auth/register":
            full_name = str(body.get("full_name", "")).strip()
            email = str(body.get("email", "")).strip().lower()
            phone = str(body.get("phone", "")).strip()
            password = str(body.get("password", ""))
            role = str(body.get("role", auth.CITIZEN))
            if not full_name or len(full_name) > 80:
                self._send_json({"error": "Enter your full name."}, 400); return
            if not auth.EMAIL_RE.match(email) or len(email) > 120:
                self._send_json({"error": "Enter a valid email address."}, 400); return
            if len(phone) < 6 or len(phone) > 25:
                self._send_json({"error": "Enter a valid phone number."}, 400); return
            if len(password) < auth.MIN_PASSWORD_LEN:
                self._send_json({"error": f"Password must be at least {auth.MIN_PASSWORD_LEN} characters."}, 400); return
            if role not in auth.SELF_SERVICE_ROLES:
                self._send_json({"error": "You can only register as a citizen or a volunteer."}, 400); return
            if conn.execute("SELECT 1 FROM users WHERE lower(email) = ?", (email,)).fetchone():
                self._send_json({"error": "An account with this email already exists."}, 409); return

            usr_id = f"usr-{str(uuid.uuid4())[:8]}"
            conn.execute("INSERT INTO users VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (usr_id, full_name, phone, email, role, 1, now))
            conn.execute("INSERT INTO credentials (user_id, password_hash, created_at) VALUES (?, ?, ?)",
                         (usr_id, auth.hash_password(password), now))
            if role == auth.VOLUNTEER:
                loc_id = f"loc-{usr_id}"
                conn.execute("INSERT INTO locations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                             (loc_id, 13.0800, 80.2700, "Volunteer Staging Post", "Central Area", 0, None, None, now))
                conn.execute("""
                INSERT INTO volunteers (id, user_id, skills_json, location_id, status, emergency_contact, rating, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (f"vol-{str(uuid.uuid4())[:8]}", usr_id, json.dumps(["first_aid"]), loc_id, "available", phone, 5.0, now))
            log_audit(conn, "user", usr_id, "USER_REGISTERED", {"role": role}, full_name)
            conn.commit()
            token = auth.create_session(conn, usr_id)
            self._send_json({"user": auth.user_for_token(conn, token)}, 201,
                            {"Set-Cookie": auth.session_cookie(token, secure)})

        elif path == "/api/auth/logout":
            auth.delete_session(conn, self._token())
            self._send_json({"status": "signed_out"}, 200, {"Set-Cookie": auth.clear_cookie(secure)})

        else:
            self._send_json({"error": "Route not found"}, 404)

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
            # Frontend files: only the login page assets are public
            public = path in ("/login.html", "/login.css", "/login.js", "/styles.css")
            authed = self._authenticate() is not None
            if path in ("/", "", "/index.html"):
                if not authed:
                    self._redirect("/login.html")
                    return
                self.path = "/index.html"
            elif path == "/login.html" and authed:
                self._redirect("/")
                return
            elif not public and not authed:
                self._redirect("/login.html")
                return
            super().do_GET()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path.startswith("/api/"):
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
    # GET HANDLERS
    # =========================================================================
    def _handle_api_get(self, path: str, query: Dict[str, list]):
        conn = get_connection()
        cur = conn.cursor()
        try:
            if path == "/api/auth/me":
                user = self._authenticate(conn)
                if user:
                    self._send_json({"user": user})
                else:
                    self._send_json({"error": "Not signed in."}, 401)
                return
            if not self._authorize(conn, "GET", path):
                return

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
                clauses = []
                if status_filter:
                    clauses.append("r.status = ?")
                    params.append(status_filter)
                if self._user["role"] == auth.CITIZEN:
                    clauses.append("r.requester_id = ?")
                    params.append(self._user["id"])
                if clauses:
                    sql += " WHERE " + " AND ".join(clauses)
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

            elif path == "/api/dispatches":
                sql = """
                SELECT d.*, u.full_name as volunteer_name,
                       r.contact_name as request_name, r.category as request_category,
                       i.item_name, i.unit
                FROM dispatch_requests d
                JOIN volunteers v ON d.volunteer_id = v.id
                JOIN users u ON v.user_id = u.id
                JOIN help_requests r ON d.help_request_id = r.id
                LEFT JOIN inventory_items i ON d.inventory_item_id = i.id
                """
                params = []
                if self._user["role"] == auth.VOLUNTEER:
                    sql += " WHERE v.user_id = ?"
                    params.append(self._user["id"])
                sql += " ORDER BY d.created_at DESC"
                cur.execute(sql, params)
                self._send_json([dict(r) for r in cur.fetchall()])

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
            if path.startswith("/api/auth/"):
                self._handle_auth_post(conn, path, body)
                return
            if not self._authorize(conn, "POST", path):
                return

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
                    id, incident_id, requester_id, contact_name, contact_phone, location_id,
                    category, severity_level, vulnerability_level, people_count, infants_count,
                    elderly_count, medical_urgency_notes, water_level_meters, calculated_priority_score,
                    status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    req_id, incident_id, self._user["id"], contact_name, contact_phone, loc_id,
                    category, severity, vulnerability, people_count, infants_count,
                    elderly_count, medical_notes, water_level, score,
                    "submitted", now, now
                ))

                log_audit(conn, "help_request", req_id, "REQUEST_SUBMITTED", {
                    "contact_name": contact_name,
                    "category": category,
                    "severity": severity,
                    "priority_score": score
                }, self._user["full_name"])

                conn.commit()
                self._send_json({"status": "created", "request_id": req_id, "priority_score": score, "score_factors": breakdown}, 201)

            # 2. Coordinator Verification
            elif path.startswith("/api/requests/") and path.endswith("/verify"):
                req_id = path.split("/")[3]
                action = body.get("action", "verified") # "verified" or "rejected"
                notes = body.get("notes", "")
                coordinator_name = self._user["full_name"]

                cur.execute("SELECT * FROM help_requests WHERE id = ?", (req_id,))
                req = cur.fetchone()
                if not req:
                    self._send_json({"error": "Request not found"}, 404)
                    return

                new_status = "verified" if action == "verified" else "rejected"
                cur.execute("UPDATE help_requests SET status = ?, notes = ?, verified_by = ?, verified_at = ?, updated_at = ? WHERE id = ?",
                            (new_status, notes, self._user["id"], now, now, req_id))
                
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
                }, self._user["full_name"])
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
                conn2 = get_connection()
                token = auth.create_session(conn2, self._user["id"])
                conn2.close()
                self._send_json({"status": "reset_successful", "message": "Database reset to baseline active crisis."},
                                200, {"Set-Cookie": auth.session_cookie(token, self._secure_request())})

            # 7. Volunteer Dispatch Submission
            elif path == "/api/dispatches":
                vol = cur.execute(
                    "SELECT id FROM volunteers WHERE user_id = ?", (self._user["id"],)
                ).fetchone()
                if not vol and self._user["role"] == auth.VOLUNTEER:
                    self._send_json({"error": "Volunteer profile not found."}, 404)
                    return
                if self._user["role"] == auth.VOLUNTEER:
                    volunteer_id = vol["id"]
                else:
                    volunteer_id = body.get("volunteer_id", "")
                if not volunteer_id:
                    self._send_json({"error": "Choose a volunteer."}, 400)
                    return

                request_id = str(body.get("help_request_id", "")).strip()
                item_id = str(body.get("inventory_item_id", "")).strip() or None
                quantity = int(body.get("quantity", 0))
                destination = str(body.get("destination", "")).strip()
                notes = str(body.get("notes", "")).strip()

                req = cur.execute(
                    "SELECT id, status FROM help_requests WHERE id = ?", (request_id,)
                ).fetchone()
                if not req:
                    self._send_json({"error": "Help request not found."}, 404)
                    return
                if req["status"] in ("resolved", "rejected"):
                    self._send_json({"error": "This request is no longer open for dispatch."}, 400)
                    return

                if item_id:
                    item = cur.execute(
                        "SELECT quantity_available FROM inventory_items WHERE id = ?", (item_id,)
                    ).fetchone()
                    if not item:
                        self._send_json({"error": "Inventory item not found."}, 404)
                        return
                    if quantity <= 0 or quantity > item["quantity_available"]:
                        self._send_json({"error": "Quantity must be available in stock."}, 400)
                        return
                    cur.execute(
                        "UPDATE inventory_items SET quantity_available = quantity_available - ?, "
                        "quantity_reserved = quantity_reserved + ?, updated_at = ? WHERE id = ?",
                        (quantity, quantity, now, item_id),
                    )

                dispatch_id = f"DSP-{str(uuid.uuid4())[:8].upper()}"
                cur.execute("""
                    INSERT INTO dispatch_requests
                    (id, volunteer_id, help_request_id, inventory_item_id, quantity, destination, notes, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'submitted', ?, ?)
                """, (dispatch_id, volunteer_id, request_id, item_id, quantity, destination, notes, now, now))
                cur.execute("""
                    INSERT INTO assignment_history
                    (id, volunteer_id, help_request_id, assigned_by, instructions, assigned_at, handover_notes, audit_trail)
                    VALUES (?, ?, ?, ?, ?, ?, '', '[]')
                """, (
                    f"ASN-{str(uuid.uuid4())[:8].upper()}",
                    volunteer_id,
                    request_id,
                    self._user["id"],
                    notes or "Volunteer dispatch submitted",
                    now,
                ))
                cur.execute(
                    "UPDATE help_requests SET status = 'in_progress', updated_at = ? WHERE id = ?",
                    (now, request_id),
                )
                log_audit(conn, "dispatch", dispatch_id, "DISPATCH_SUBMITTED", {
                    "help_request_id": request_id,
                    "inventory_item_id": item_id,
                    "quantity": quantity,
                    "destination": destination,
                }, self._user["full_name"])
                conn.commit()
                self._send_json({"status": "submitted", "dispatch_id": dispatch_id}, 201)

            # 8. Volunteer Registration
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

                log_audit(conn, "shelter", shl_id, "SHELTER_CREATED", {"name": name, "capacity": capacity}, self._user["full_name"])
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
            if not self._authorize(conn, "PUT", path):
                return
            # Shelter Capacity Update (by shelter manager)
            if path.startswith("/api/shelters/") and path.endswith("/capacity"):
                shelter_id = path.split("/")[3]
                new_occupancy = int(body.get("current_occupancy", 0))
                reason = body.get("reason", "Manual shelter intake / check-out update")
                manager_name = self._user["full_name"]

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
                """, (str(uuid.uuid4()), shelter_id, prev_occ, new_occupancy, delta, reason, self._user["id"], now))

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
                log_audit(conn, "inventory", item_id, "RESTOCK", {"added": additional_qty}, self._user["full_name"])
                conn.commit()
                self._send_json({"status": "restocked", "item_id": item_id})

            else:
                self._send_json({"error": "Route not found"}, 404)
        finally:
            conn.close()


def run_server(port: int = 8080, host: Optional[str] = None):
    init_db(seed_sample_data=True)
    # Hosting services set PORT and need 0.0.0.0; locally stay on this computer only
    host = host or ("0.0.0.0" if "PORT" in os.environ else "127.0.0.1")
    server_address = (host, port)
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
