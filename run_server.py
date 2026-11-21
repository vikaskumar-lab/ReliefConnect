#!/usr/bin/env python3
"""
ReliefConnect Entry Point
Starts the multi-threaded HTTP server and opens the dashboard in browser.
"""

import sys
import os
import webbrowser

# Add current folder to sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backend.server import run_server

if __name__ == "__main__":
    # Hosting services (like Render) provide the port in the PORT variable
    hosted = "PORT" in os.environ
    if len(sys.argv) > 1:
        port = int(sys.argv[1])
    else:
        port = int(os.environ.get("PORT", 8080))
    url = f"http://localhost:{port}"

    print("=" * 70)
    print("  RELIEFCONNECT: EMERGENCY OPERATIONS & ALLOCATION SYSTEM")
    print("  Disaster Relief Coordination Prototype")
    print("=" * 70)
    print(f"  * Web Dashboard: {url}")
    print(f"  * API Endpoint:  {url}/api/dashboard/stats")
    print("  * Safety Banner: Active (Training prototype notice enabled)")
    print("  * Concurrency:   Active (Zero-double-allocation locking enabled)")
    print("=" * 70)
    print("  Press Ctrl+C to stop server.\n")

    if not hosted:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    run_server(port)
