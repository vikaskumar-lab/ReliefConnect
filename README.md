# ReliefConnect | Disaster Relief Coordination Prototype
**Emergency Operations System**

ReliefConnect coordinates emergency help requests, shelters, volunteers, and relief supplies during disaster emergencies (e.g. floods). It features a multi-criteria prioritization algorithm, constrained spatial allocation, double-allocation prevention, and an immutable decision audit trail.

---

## 🚀 Quick Start in Command Prompt (CMD)

Open **Command Prompt (`cmd.exe`)** and run:

```cmd
cd "C:\Users\VIKAS KUMAR\.gemini\antigravity\scratch\reliefconnect"
python run_server.py
```

Open your browser to:
👉 **[http://localhost:8080](http://localhost:8080)**

---

## 🧪 Running the 100+ Flood Simulation Test Suite in CMD

To run the automated validation suite testing 120 synthetic flood requests, concurrency locking, and algorithmic fairness:

```cmd
cd "C:\Users\VIKAS KUMAR\.gemini\antigravity\scratch\reliefconnect"
python test_suite.py
```

### What the Test Suite Evaluates:
1. **Schema Initialization**: Verifies all 10 core entities (`users`, `incidents`, `locations`, `shelters`, `capacity_updates`, `inventory_items`, `help_requests`, `volunteers`, `allocations`, `assignment_history`).
2. **PostGIS / Haversine Distance**: Verifies great-circle geodesic distance calculations.
3. **Prioritization Scoring**: Verifies dynamic urgency weighting:
   $$S_{\text{req}} = 0.50 \cdot \text{Severity} + 0.30 \cdot \text{Vulnerability} + 0.15 \cdot \text{WaterLevel} + 0.05 \cdot \text{WaitDecay}$$
4. **100+ Synthetic Request Generation**: Spawns 120 requests across 3 realistic flood zones (Riverside Delta, Urban Basin, Suburbs).
5. **Zero Double-Allocation Guarantee**: Verifies strict 0 duplicate allocations across concurrent resources.
6. **Shelter & Inventory Invariants**: Verifies occupancy does not exceed total capacity and inventory balances stay positive.
7. **Explainability Audit Trail**: Verifies structured rationale logs explaining why each allocation decision was made.

---

## 📂 Project Architecture

```
reliefconnect/
├── backend/
│   ├── models.py           # Domain models, geospatial calculations & enums
│   ├── database.py         # 10 core entities, SQLite spatial engine & transaction locks
│   ├── algorithm.py        # Multi-criteria prioritization & constrained allocation engine
│   ├── simulator.py        # 100+ synthetic flood request generator & validation auditor
│   └── server.py           # Multi-threaded REST API server (Python stdlib)
├── frontend/
│   ├── index.html          # Interactive Emergency Operations Center Web Dashboard
│   ├── styles.css          # High-contrast disaster response theme & responsive styles
│   └── app.js              # Real-time state management, Leaflet/Vector maps, modals
├── schema_postgres_postgis.sql  # Production PostgreSQL + PostGIS schema DDL
├── run_server.py           # Main application launcher
├── test_suite.py           # Automated CLI validation runner
└── README.md               # Documentation & usage guide
```

---

## 🛡️ Safety & Ethics Safeguards

- **Emergency Notice Banner**: Persistent alert stating this is a training prototype and giving official emergency dispatch numbers (112 / 911).
- **Location Privacy Masking**: Toggle available on citizen request form that applies a ~500m fuzzy perimeter to obfuscate exact household coordinates for vulnerable persons while preserving district-level triage accuracy.

## ReliefConnect UI refresh (prototype milestone)

The frontend now includes a redesigned navy/cyan visual theme and a sign-in-style demo entry screen. To preview it, start the existing Python server using the project's normal run command and open the local application in your browser.

**Important authentication limitation:** the current sign-in screen is only a frontend demonstration gate. It accepts any non-empty email/password and stores the selected demo role in the browser session. It does not authenticate identities, enforce permissions, or protect API endpoints. Do not expose this prototype publicly or use it for real emergency operations. Production authentication must be implemented server-side (password hashing, sessions/JWT, role checks, CSRF protections, rate limiting and audit logging) before deployment.

The existing backend, SQLite database, map, allocation workflow, and audit functionality have been retained in this milestone. React, FastAPI, PostgreSQL/PostGIS migration, real GPS tracking, feedback APIs, advanced search, notifications and fraud detection are not yet implemented; they are planned follow-up phases rather than completed features.
