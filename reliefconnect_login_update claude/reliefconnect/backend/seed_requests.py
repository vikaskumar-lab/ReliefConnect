"""Baseline help requests used to demonstrate the dashboard workflow."""

import json
import uuid


def seed_help_requests(conn, incident_id, now):
    requests = [
        ("Ananya Rao", "+91-98400-60001", 13.0890, 80.2690, "evacuation", 5, 5, 4, 2.8, "submitted"),
        ("Krish Menon", "+91-98400-60002", 13.0840, 80.2780, "medical", 4, 4, 2, 1.8, "verified"),
        ("Meera Iyer", "+91-98400-60003", 13.0710, 80.2890, "evacuation", 3, 3, 5, 1.2, "submitted"),
        ("Arjun Das", "+91-98400-60004", 13.0580, 80.2550, "supplies", 2, 2, 3, 0.7, "submitted"),
        ("Leela Narayan", "+91-98400-60005", 13.0950, 80.2600, "medical", 5, 5, 1, 3.4, "verified"),
        ("Vikram Shah", "+91-98400-60006", 13.0780, 80.2720, "evacuation", 4, 3, 6, 2.1, "submitted"),
    ]

    for index, (name, phone, lat, lon, category, severity, vulnerability, people, water, status) in enumerate(requests):
        location_id = f"loc-request-{index + 1}"
        conn.execute(
            "INSERT OR IGNORE INTO locations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                location_id,
                lat,
                lon,
                f"Baseline flood area {index + 1}",
                "Affected Sector",
                0,
                None,
                None,
                now,
            ),
        )
        priority = 0.50 * severity + 0.30 * vulnerability + 0.15 * min(5.0, water)
        conn.execute(
            """
            INSERT OR IGNORE INTO help_requests (
                id, incident_id, requester_id, contact_name, contact_phone,
                location_id, category, severity_level, vulnerability_level,
                people_count, infants_count, elderly_count,
                medical_urgency_notes, water_level_meters,
                calculated_priority_score, status, notes, created_at, updated_at
            ) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"req-baseline-{index + 1}",
                incident_id,
                name,
                phone,
                location_id,
                category,
                severity,
                vulnerability,
                people,
                1 if index % 3 == 0 else 0,
                1 if index % 2 == 0 else 0,
                "Needs immediate coordination" if category == "medical" else "",
                water,
                round(priority, 4),
                status,
                json.dumps({"source": "baseline_seed"}),
                now - (index + 1) * 1800,
                now,
            ),
        )
