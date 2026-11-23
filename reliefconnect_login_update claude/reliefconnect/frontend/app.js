/**
 * ReliefConnect Frontend Application Logic
 * Disaster Relief Coordination Prototype
 */

const API_BASE = ""; // Relative URL since served from same port

let mapInstance = null;
let mapMarkers = [];
let cachedRequests = [];
let cachedShelters = [];

// SESSION & ROLES: a 401 from any API call sends the user to the login page
const _nativeFetch = window.fetch.bind(window);
window.fetch = async (...args) => {
  const res = await _nativeFetch(...args);
  if (res.status === 401) window.location.href = "/login.html";
  return res;
};

let currentUser = null;
const ROLE_LABELS = {
  administrator: "Administrator", relief_coordinator: "Relief Coordinator",
  shelter_manager: "Shelter Manager", volunteer: "Volunteer", citizen: "Citizen"
};
const STAFF_ROLES = ["administrator", "relief_coordinator", "shelter_manager", "volunteer"];
const FIELD_ROLES = ["administrator", "relief_coordinator", "shelter_manager"];
const OPS_ROLES = ["administrator", "relief_coordinator"];

function can(...roles) {
  return !!currentUser && roles.includes(currentUser.role);
}

async function loadCurrentUser() {
  const res = await fetch(`${API_BASE}/api/auth/me`);
  if (!res.ok) return;
  currentUser = (await res.json()).user;

  document.getElementById("user-name").textContent = currentUser.full_name;
  document.getElementById("user-role").textContent = ROLE_LABELS[currentUser.role] || currentUser.role;
  document.getElementById("user-chip").style.display = "flex";

  // Hide tabs this role may not use (indexes match switchTab)
  const tabs = document.querySelectorAll(".nav-tabs .tab-btn");
  if (!can(...STAFF_ROLES) && tabs[4]) tabs[4].style.display = "none";
  if (!can(...OPS_ROLES) && tabs[5]) tabs[5].style.display = "none";
  if (!can(...FIELD_ROLES) && tabs[6]) tabs[6].style.display = "none";
  if (currentUser.role === "volunteer") {
    tabs.forEach((tab, index) => { if (index !== 4) tab.style.display = "none"; });
  }

  // Hide buttons restricted to certain roles
  document.querySelectorAll("[data-roles]").forEach(el => {
    if (!el.dataset.roles.split(" ").includes(currentUser.role)) el.style.display = "none";
  });

  // Pre-fill the help request form with the signed-in person's details
  const nameInput = document.getElementById("form-name");
  const phoneInput = document.getElementById("form-phone");
  if (nameInput && !nameInput.value) nameInput.value = currentUser.full_name;
  if (phoneInput && !phoneInput.value) phoneInput.value = currentUser.phone || "";

  if (currentUser.role === "citizen") switchTab("citizen-tab");
  if (currentUser.role === "volunteer") switchTab("volunteers-tab");
}

async function logout() {
  await _nativeFetch(`${API_BASE}/api/auth/logout`, { method: "POST" });
  window.location.href = "/login.html";
}

document.addEventListener("DOMContentLoaded", async () => {
  await loadCurrentUser();
  initMap();
  fetchDashboardStats();
  loadRequests();
  loadShelters();
  if (can(...STAFF_ROLES)) loadVolunteers();
  if (currentUser && currentUser.role === "volunteer") loadVolunteerDispatch();
  if (can(...FIELD_ROLES)) loadAuditTrail();
  updateFormScorePreview();

  // Polling every 15 seconds for live emergency updates
  setInterval(() => {
    fetchDashboardStats();
  }, 15000);
});

// TAB SWITCHING
function switchTab(tabId) {
  document.querySelectorAll(".tab-content").forEach(el => el.classList.remove("active"));
  document.querySelectorAll(".nav-tabs .tab-btn").forEach(btn => btn.classList.remove("active"));

  const target = document.getElementById(tabId);
  if (target) target.classList.add("active");

  const btnIndex = {
    "map-tab": 0, "requests-tab": 1, "citizen-tab": 2,
    "shelters-tab": 3, "volunteers-tab": 4, "simulation-tab": 5, "audit-tab": 6
  }[tabId] || 0;

  const btn = document.querySelectorAll(".nav-tabs .tab-btn")[btnIndex];
  if (btn) btn.classList.add("active");

  if (tabId === "map-tab" && mapInstance && typeof mapInstance.invalidateSize === "function") {
    setTimeout(() => mapInstance.invalidateSize(), 200);
  }
}

// 1. MAP INITIALIZATION & RENDERING
function initMap() {
  const mapContainer = document.getElementById("map-view");
  if (!mapContainer) return;

  const defaultCenter = [13.0827, 80.2707]; // Chennai flood coordinates

  if (typeof L !== "undefined") {
    // Leaflet is available
    try {
      // Zoom ONLY through the (+) / (-) buttons: every other zoom gesture is disabled.
      mapInstance = L.map("map-view", {
        zoomControl: true,        // the + / - buttons
        scrollWheelZoom: false,   // no zoom on mouse wheel (page scrolls normally)
        doubleClickZoom: false,   // no zoom on double click
        touchZoom: false,         // no pinch zoom
        boxZoom: false,           // no shift + drag zoom
        keyboard: false,          // no +/- keyboard zoom
        zoomSnap: 1,
        zoomDelta: 1,
        minZoom: 10,
        maxZoom: 18
      }).setView(defaultCenter, 12);   // fixed start view: nothing auto-zooms later
      L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        attribution: '&copy; OpenStreetMap contributors'
      }).addTo(mapInstance);

      // Keep the map fitted to its container when the window is resized
      window.addEventListener("resize", () => mapInstance.invalidateSize());
      setTimeout(() => mapInstance.invalidateSize(), 250);

      // Add click listener on map to auto-fill latitude and longitude for citizens
      mapInstance.on("click", (e) => {
        const latInput = document.getElementById("form-lat");
        const lonInput = document.getElementById("form-lon");
        if (latInput && lonInput) {
          latInput.value = e.latlng.lat.toFixed(4);
          lonInput.value = e.latlng.lng.toFixed(4);
        }
      });
      return;
    } catch (err) {
      console.warn("Leaflet tile error, falling back to SVG vector canvas:", err);
    }
  }

  // Fallback interactive vector canvas if offline
  renderOfflineCanvasMap(mapContainer);
}

function renderOfflineCanvasMap(container) {
  container.innerHTML = `
    <div style="width:100%; height:100%; background:#111827; display:flex; flex-direction:column; justify-content:center; align-items:center; position:relative; overflow:hidden;" id="svg-map-wrapper">
      <div style="position:absolute; top:12px; left:12px; background:rgba(0,0,0,0.7); padding:6px 12px; border-radius:4px; font-size:12px; border:1px solid #334155; z-index:10;">
        📍 Integrated Vector Tactical Map (Lat 13.04–13.11, Lon 80.22–80.29)
      </div>
      <svg id="vector-map-svg" width="100%" height="100%" viewBox="0 0 1000 600" style="background:#0f172a;">
        <defs>
          <pattern id="grid" width="50" height="50" patternUnits="userSpaceOnUse">
            <path d="M 50 0 L 0 0 0 50" fill="none" stroke="#1e293b" stroke-width="1"/>
          </pattern>
        </defs>
        <rect width="1000" height="600" fill="url(#grid)" />
        <!-- River path -->
        <path d="M 150 0 Q 300 250 500 300 T 850 600" fill="none" stroke="#1d4ed8" stroke-width="26" opacity="0.35"/>
        <g id="svg-markers-layer"></g>
      </svg>
    </div>
  `;
}

function updateMapMarkers() {
  if (mapInstance && typeof mapInstance.addLayer === "function") {
    // Clear old markers
    mapMarkers.forEach(m => mapInstance.removeLayer(m));
    mapMarkers = [];

    // Add Shelters
    cachedShelters.forEach(s => {
      const isFull = s.current_occupancy >= s.total_capacity;
      const isEvac = s.status === "evacuating";
      const iconColor = isEvac ? "purple" : (isFull ? "red" : "green");

      const marker = L.circleMarker([s.lat, s.lon], {
        radius: 12,
        fillColor: iconColor === "green" ? "#10b981" : (iconColor === "red" ? "#ef4444" : "#a855f7"),
        color: "#ffffff",
        weight: 2,
        opacity: 1,
        fillOpacity: 0.9
      }).addTo(mapInstance);

      marker.bindPopup(`
        <div style="font-family:sans-serif; font-size:12px;">
          <strong style="font-size:14px;">🏢 ${s.name}</strong><br>
          <b>Status:</b> ${s.status.toUpperCase()}<br>
          <b>Occupancy:</b> ${s.current_occupancy} / ${s.total_capacity} beds<br>
          <b>Medical Unit:</b> ${s.has_medical_unit ? "Yes" : "No"}<br>
          <b>Contact:</b> ${s.contact_phone || "N/A"}<br>
          <hr style="margin:6px 0; border:0; border-top:1px solid #ccc;">
          ${can(...FIELD_ROLES) ? `<button style="background:#2563eb; color:white; border:none; padding:4px 8px; border-radius:4px; cursor:pointer;" onclick="openCapacityModal('${s.id}', ${s.current_occupancy})">Edit Occupancy</button>` : ""}
        </div>
      `);
      mapMarkers.push(marker);
    });

    // Add Requests: only open cases (resolved / rejected are hidden to prevent clutter)
    openRequestsForMap().slice(0, 60).forEach(r => {
      const lat = r.display_lat;
      const lon = r.display_lon;
      const sevColor = r.severity_level >= 5 ? "#ef4444" : (r.severity_level === 4 ? "#f97316" : (r.severity_level === 3 ? "#f59e0b" : "#3b82f6"));

      const marker = L.circleMarker([lat, lon], {
        radius: r.is_privacy_masked ? 8 : 6,
        fillColor: sevColor,
        color: r.is_privacy_masked ? "#38bdf8" : "#ffffff",
        weight: r.is_privacy_masked ? 2 : 1,
        dashArray: r.is_privacy_masked ? "3, 3" : null,
        fillOpacity: 0.85
      }).addTo(mapInstance);

      marker.bindPopup(`
        <div style="font-family:sans-serif; font-size:12px;">
          <strong style="color:${sevColor}; font-size:13px;">🚨 ${r.id} (Severity ${r.severity_level})</strong><br>
          <b>Requester:</b> ${r.contact_name}<br>
          <b>Need:</b> ${r.category.replace(/_/g, ' ')}<br>
          <b>People:</b> ${r.people_count} (Infants: ${r.infants_count}, Elderly: ${r.elderly_count})<br>
          <b>Water Depth:</b> ${r.water_level_meters}m<br>
          <b>Priority Score:</b> ${r.calculated_priority_score.toFixed(2)}<br>
          <b>Status:</b> ${r.status.toUpperCase()}<br>
          ${r.is_privacy_masked ? '<span style="color:#0284c7;">🛡️ Location Privacy Masked</span><br>' : ''}
          ${r.decision_rationale ? `<button style="margin-top:6px; background:#1e293b; color:#38bdf8; border:1px solid #38bdf8; padding:3px 8px; border-radius:4px; cursor:pointer;" onclick="openExplainModal('${r.id}')">View Allocation Rationale</button>` : ''}
        </div>
      `);
      mapMarkers.push(marker);
    });
  } else {
    // Render on SVG fallback
    const svgGroup = document.getElementById("svg-markers-layer");
    if (!svgGroup) return;
    svgGroup.innerHTML = "";

    const project = (lat, lon) => {
      const minLat = 13.04, maxLat = 13.11;
      const minLon = 80.22, maxLon = 80.30;
      const x = ((lon - minLon) / (maxLon - minLon)) * 960 + 20;
      const y = (1.0 - (lat - minLat) / (maxLat - minLat)) * 560 + 20;
      return [x, y];
    };

    // Plot Shelters
    cachedShelters.forEach(s => {
      const [x, y] = project(s.lat, s.lon);
      const isFull = s.current_occupancy >= s.total_capacity;
      const color = isFull ? "#ef4444" : "#10b981";
      svgGroup.innerHTML += `
        <g style="cursor:pointer;" onclick="alert('Shelter: ${s.name}\\nCapacity: ${s.current_occupancy}/${s.total_capacity}')">
          <circle cx="${x}" cy="${y}" r="12" fill="${color}" stroke="#ffffff" stroke-width="2"/>
          <text x="${x}" y="${y - 15}" fill="#ffffff" font-size="11" font-weight="bold" text-anchor="middle">${s.name.substring(0, 16)}</text>
        </g>
      `;
    });

    // Plot Requests
    openRequestsForMap().slice(0, 60).forEach(r => {
      const [x, y] = project(r.display_lat, r.display_lon);
      const color = r.severity_level >= 5 ? "#ef4444" : (r.severity_level === 4 ? "#f97316" : "#f59e0b");
      svgGroup.innerHTML += `
        <circle cx="${x}" cy="${y}" r="6" fill="${color}" stroke="${r.is_privacy_masked ? '#38bdf8' : '#fff'}" stroke-width="${r.is_privacy_masked ? '2' : '1'}" opacity="0.8"/>
      `;
    });
  }
}

function openRequestsForMap() {
  return cachedRequests.filter(r => r.status !== "resolved" && r.status !== "rejected");
}

// 2. DASHBOARD METRICS
async function fetchDashboardStats() {
  try {
    const res = await fetch(`${API_BASE}/api/dashboard/stats`);
    const data = await res.json();

    if (data.incident) {
      document.getElementById("incident-name-text").innerText = `${data.incident.name} (Severity Level ${data.incident.severity_level})`;
    }

    if (data.requests) {
      document.getElementById("stat-total-requests").innerText = data.requests.total_requests || 0;
      document.getElementById("stat-people-count").innerText = data.requests.total_people_impacted || 0;
      const q = data.requests;
      document.getElementById("stat-req-breakdown").innerText =
        `${q.submitted_count || 0} submitted / ${q.verified_count || 0} verified / ` +
        `${q.allocated_count || 0} allocated / ${q.resolved_count || 0} resolved`;
    }

    if (data.shelters) {
      const total = data.shelters.total_bed_capacity || 0;
      const occ = data.shelters.total_current_occupancy || 0;
      const pct = total > 0 ? ((occ / total) * 100).toFixed(1) : 0;
      document.getElementById("stat-shelter-occupancy").innerText = `${occ} / ${total}`;
      document.getElementById("stat-shelter-pct").innerText = `${pct}% regional capacity occupied`;
    }

    if (data.volunteers) {
      document.getElementById("stat-volunteers").innerText = data.volunteers.available_volunteers || 0;
      document.getElementById("stat-vol-deployed").innerText = `${data.volunteers.assigned_volunteers || 0} currently deployed`;
    }
  } catch (err) {
    console.error("Failed to load dashboard metrics:", err);
  }
}

// 3. LOAD HELP REQUESTS
const STATUS_LABELS = {
  submitted: "Submitted", verified: "Verified", allocated: "Allocated",
  in_progress: "In Progress", dispatched: "Dispatched", resolved: "Resolved", rejected: "Rejected"
};

// Fetch ALL requests once; the status filter / chips only change what is displayed,
// so the table, the chips, the map and the top statistics always agree.
async function loadRequests() {
  try {
    const res = await fetch(`${API_BASE}/api/requests`);
    cachedRequests = await res.json();
    updateMapMarkers();
    renderRequests();
  } catch (err) {
    console.error("Error loading requests:", err);
  }
}

function setStatusFilter(status) {
  document.getElementById("filter-status-select").value = status;
  renderRequests();
}

function renderRequests() {
  const statusFilter = document.getElementById("filter-status-select").value;
  const requests = statusFilter ? cachedRequests.filter(r => r.status === statusFilter) : cachedRequests;

  // Status chips with live counts
  const counts = {};
  cachedRequests.forEach(r => { counts[r.status] = (counts[r.status] || 0) + 1; });
  const chips = [["", "All"], ["submitted", "Submitted"], ["verified", "Verified"], ["allocated", "Allocated"], ["resolved", "Resolved"]];
  document.getElementById("status-chips").innerHTML = chips.map(([key, label]) => {
    const n = key ? (counts[key] || 0) : cachedRequests.length;
    return `<button class="status-chip ${statusFilter === key ? "active" : ""}" onclick="setStatusFilter('${key}')">${label}<strong>${n}</strong></button>`;
  }).join("");

  const tbody = document.getElementById("requests-tbody");
  if (requests.length === 0) {
    const label = statusFilter ? `${STATUS_LABELS[statusFilter] || statusFilter} ` : "";
    tbody.innerHTML = `<tr><td colspan="10" style="text-align:center; color:#94a3b8;">No ${label}help requests recorded.</td></tr>`;
    return;
  }

  tbody.innerHTML = requests.map(r => {
    const sevClass = `badge-sev-${r.severity_level}`;
    const statusClass = `badge-status-${r.status}`;
    const hasRationale = !!r.decision_rationale;
    const created = r.created_at ? new Date(r.created_at * 1000).toLocaleString([], { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : "";

    let decision;
    if (r.allocated_shelter_name) {
      const verb = r.status === "resolved" ? "Resolved at" : "Allocated to";
      decision = `<span>🏢 ${verb} ${escapeHtml(r.allocated_shelter_name)}</span>`;
    } else if (r.status === "submitted") {
      decision = '<span style="color:#f59e0b;">⏳ Awaiting verification</span>';
    } else if (r.status === "verified") {
      decision = '<span style="color:#60a5fa;">✔ Ready for allocation</span>';
    } else {
      decision = '<span style="color:#94a3b8;">Unassigned</span>';
    }

    return `
      <tr>
        <td><strong style="color:#60a5fa;">${r.id}</strong><br><span class="req-sub">${created}</span></td>
        <td>
          <strong>${escapeHtml(r.contact_name)}</strong><br>
          <span class="req-sub">${escapeHtml(r.contact_phone || "N/A")}</span><br>
          <span class="req-sub">📍 ${escapeHtml(r.address_text || "")}</span>
        </td>
        <td>${formatCategory(r.category)}</td>
        <td><span class="badge ${sevClass}">Lvl ${r.severity_level}</span></td>
        <td><strong style="color:#f59e0b;">${r.calculated_priority_score.toFixed(2)}</strong></td>
        <td><strong>${r.people_count}</strong><br><span class="req-sub">${r.infants_count} infants / ${r.elderly_count} elderly</span></td>
        <td>${r.water_level_meters}m</td>
        <td><span class="badge ${statusClass}">${STATUS_LABELS[r.status] || r.status}</span></td>
        <td>
          ${decision}
          ${hasRationale ? `<br><a href="javascript:void(0)" onclick="openExplainModal('${r.id}')" style="color:#38bdf8; font-size:11px;">Why allocated? ↗</a>` : ""}
        </td>
        <td>
          <div style="display:flex; gap:4px;">
            ${r.status === "submitted" && can(...OPS_ROLES) ? `
              <button class="btn btn-outline btn-sm" onclick="verifyRequest('${r.id}', 'verified')">Verify</button>
              <button class="btn btn-outline btn-sm" style="color:#ef4444;" onclick="verifyRequest('${r.id}', 'rejected')">Reject</button>
            ` : ""}
            ${hasRationale ? `<button class="btn btn-outline btn-sm" onclick="openExplainModal('${r.id}')">Audit</button>` : ""}
          </div>
        </td>
      </tr>
    `;
  }).join("");
}

// 4. LOAD SHELTERS & INVENTORY
async function loadShelters() {
  try {
    const res = await fetch(`${API_BASE}/api/shelters`);
    const shelters = await res.json();
    cachedShelters = shelters;
    updateMapMarkers();

    const grid = document.getElementById("shelters-grid");
    grid.innerHTML = shelters.map(s => {
      const pct = s.total_capacity > 0 ? ((s.current_occupancy / s.total_capacity) * 100).toFixed(0) : 0;
      const isFull = s.current_occupancy >= s.total_capacity;
      const progressClass = isFull ? "danger" : (pct > 75 ? "warning" : "success");

      return `
        <div class="stat-card" style="border-left: 4px solid ${isFull ? '#ef4444' : '#10b981'};">
          <div style="display:flex; justify-content:space-between; align-items:flex-start;">
            <strong style="font-size:15px; color:#fff;">${escapeHtml(s.name)}</strong>
            <span class="badge ${isFull ? 'badge-sev-5' : 'badge-sev-1'}">${s.status.toUpperCase()}</span>
          </div>
          <div style="font-size:12px; color:#94a3b8; margin:4px 0;">📍 ${escapeHtml(s.address_text)}</div>
          <div style="display:flex; justify-content:space-between; margin-top:8px; font-size:13px;">
            <span>Occupancy: <strong>${s.current_occupancy} / ${s.total_capacity}</strong></span>
            <span>${pct}% full</span>
          </div>
          <div class="progress-bar-container">
            <div class="progress-bar-fill ${progressClass}" style="width: ${Math.min(pct, 100)}%;"></div>
          </div>
          <div style="margin-top:10px; display:flex; gap:8px;">
            ${can(...FIELD_ROLES) ? `<button class="btn btn-outline btn-sm" onclick="openCapacityModal('${s.id}', ${s.current_occupancy})">Edit Occupancy</button>` : ""}
          </div>
        </div>
      `;
    }).join("");

    // Load Inventory table
    const invRes = await fetch(`${API_BASE}/api/inventory`);
    const inventory = await invRes.json();
    const invTbody = document.getElementById("inventory-tbody");
    invTbody.innerHTML = inventory.map(item => `
      <tr>
        <td><strong>${formatCategory(item.category)}</strong></td>
        <td>${escapeHtml(item.item_name)}</td>
        <td>${escapeHtml(item.shelter_name)}</td>
        <td><strong style="color:#10b981;">${item.quantity_available}</strong> ${item.unit}</td>
        <td><strong style="color:#f59e0b;">${item.quantity_reserved}</strong> ${item.unit}</td>
        <td>${item.quantity_available + item.quantity_reserved} ${item.unit}</td>
        <td>
          ${can(...FIELD_ROLES) ? `<button class="btn btn-outline btn-sm" onclick="restockItem('${item.id}')">+ Restock 100</button>` : ""}
        </td>
      </tr>
    `).join("");
  } catch (err) {
    console.error("Error loading shelters:", err);
  }
}

// 5. LOAD VOLUNTEERS
async function loadVolunteers() {
  try {
    const res = await fetch(`${API_BASE}/api/volunteers`);
    const volunteers = await res.json();
    const tbody = document.getElementById("volunteers-tbody");
    if (!tbody) return;
    tbody.innerHTML = volunteers.map(v => `
      <tr>
        <td><strong>${escapeHtml(v.full_name)}</strong></td>
        <td>${escapeHtml(v.phone_number)}</td>
        <td>${(v.skills || []).map(s => `<span class="badge badge-sev-2" style="margin-right:4px;">${formatCategory(s)}</span>`).join("")}</td>
        <td>${escapeHtml(v.address_text || "Field Command")}</td>
        <td><span class="badge ${v.status === 'available' ? 'badge-sev-1' : 'badge-sev-3'}">${v.status.toUpperCase()}</span></td>
      </tr>
    `).join("");
  } catch (err) {
    console.error("Error loading volunteers:", err);
  }
}

async function loadVolunteerDispatch() {
  try {
    const [requestsRes, inventoryRes, dispatchesRes] = await Promise.all([
      fetch(`${API_BASE}/api/requests`),
      fetch(`${API_BASE}/api/inventory`),
      fetch(`${API_BASE}/api/dispatches`)
    ]);
    const requests = await requestsRes.json();
    const inventory = await inventoryRes.json();
    const dispatches = await dispatchesRes.json();

    const openRequests = requests.filter(r => !["resolved", "rejected"].includes(r.status));
    const requestList = document.getElementById("volunteer-request-list");
    if (requestList) {
      requestList.innerHTML = openRequests.length ? openRequests.slice(0, 12).map(r => `
        <article class="request-card">
          <strong>${escapeHtml(r.id)} — ${formatCategory(r.category)}</strong>
          <div class="request-card-meta">Severity ${r.severity_level} · ${r.people_count} people · ${STATUS_LABELS[r.status] || r.status}</div>
          <div class="request-card-meta">${escapeHtml(r.address_text || "Affected area")}</div>
        </article>
      `).join("") : '<div class="request-card"><strong>No open requests</strong></div>';
    }

    const requestSelect = document.getElementById("dispatch-request");
    if (requestSelect) {
      requestSelect.innerHTML = openRequests.map(r =>
        `<option value="${r.id}">${r.id} — ${formatCategory(r.category)} (Severity ${r.severity_level})</option>`
      ).join("");
    }

    const available = inventory.filter(i => i.quantity_available > 0);
    const itemSelect = document.getElementById("dispatch-item");
    if (itemSelect) {
      itemSelect.innerHTML = '<option value="">No supply item — rescue/evacuation dispatch</option>' +
        available.map(i =>
          `<option value="${i.id}" data-max="${i.quantity_available}">${escapeHtml(i.item_name)} — ${i.quantity_available} ${escapeHtml(i.unit)} at ${escapeHtml(i.shelter_name)}</option>`
        ).join("");
    }

    const stockList = document.getElementById("volunteer-stock-list");
    if (stockList) {
      stockList.innerHTML = available.length ? available.slice(0, 12).map(i => `
        <article class="stock-card">
          <strong>${escapeHtml(i.item_name)}</strong>
          <div class="stock-card-meta">${i.quantity_available} ${escapeHtml(i.unit)} available · ${escapeHtml(i.shelter_name)}</div>
        </article>
      `).join("") : '<div class="stock-card"><strong>No stock currently available</strong></div>';
    }

    const dispatchTbody = document.getElementById("volunteer-dispatch-tbody");
    if (dispatchTbody) {
      dispatchTbody.innerHTML = dispatches.length ? dispatches.map(d => `
        <tr>
          <td><strong>${escapeHtml(d.id)}</strong></td>
          <td>${escapeHtml(d.help_request_id)} · ${formatCategory(d.request_category)}</td>
          <td>${d.item_name ? `${escapeHtml(d.item_name)} × ${d.quantity} ${escapeHtml(d.unit || "")}` : "Rescue crew only"}</td>
          <td>${escapeHtml(d.destination || "Not specified")}</td>
          <td><span class="badge badge-status-dispatched">${escapeHtml(d.status)}</span></td>
        </tr>
      `).join("") : '<tr><td colspan="5">No dispatch submissions yet.</td></tr>';
    }

    const itemSelectChange = () => {
      const selected = itemSelect && itemSelect.options[itemSelect.selectedIndex];
      const quantity = document.getElementById("dispatch-quantity");
      if (quantity) quantity.max = selected && selected.dataset.max ? selected.dataset.max : "";
    };
    if (itemSelect) itemSelect.addEventListener("change", itemSelectChange);
  } catch (err) {
    console.error("Error loading volunteer dispatch data:", err);
  }
}

async function handleVolunteerDispatch(event) {
  event.preventDefault();
  const requestId = document.getElementById("dispatch-request").value;
  const itemId = document.getElementById("dispatch-item").value || null;
  const quantity = parseInt(document.getElementById("dispatch-quantity").value || "0", 10);
  const destination = document.getElementById("dispatch-destination").value.trim();
  const notes = document.getElementById("dispatch-notes").value.trim();
  if (!requestId || !destination) return alert("Choose a request and enter a destination.");
  if (itemId && quantity <= 0) return alert("Enter a valid dispatch quantity.");

  try {
    const res = await fetch(`${API_BASE}/api/dispatches`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        help_request_id: requestId,
        inventory_item_id: itemId,
        quantity: itemId ? quantity : 0,
        destination,
        notes
      })
    });
    const result = await res.json();
    if (!res.ok) throw new Error(result.error || "Dispatch submission failed.");
    alert(`Dispatch submitted: ${result.dispatch_id}`);
    event.target.reset();
    loadVolunteerDispatch();
  } catch (err) {
    alert(err.message);
  }
}

// 6. LOAD AUDIT TRAIL
const AUDIT_ACTIONS = {
  INITIALIZE_CRISIS: ["Incident Declared", "#f59e0b"],
  REQUEST_SUBMITTED: ["Request Submitted", "#94a3b8"],
  REQUEST_VERIFIED: ["Request Verified", "#60a5fa"],
  REQUEST_REJECTED: ["Request Rejected", "#ef4444"],
  RESOURCES_ALLOCATED: ["Resources Allocated", "#a78bfa"],
  REQUEST_RESOLVED: ["Request Resolved", "#10b981"]
};

function summarizeAudit(l) {
  const d = l.details || {};
  const parts = [];
  const add = (k, v) => { if (v !== undefined && v !== null && v !== "") parts.push(`<b>${k}:</b> ${escapeHtml(String(v))}`); };
  switch (l.action) {
    case "INITIALIZE_CRISIS": add("Incident", d.name); add("Shelters online", d.shelters_count); break;
    case "REQUEST_SUBMITTED": add("Requester", d.contact_name); add("Need", formatCategory(d.category)); add("Severity", d.severity); add("People", d.people); add("Priority", d.priority_score); break;
    case "REQUEST_VERIFIED": add("Verified by", d.verified_by); add("Note", d.note); break;
    case "RESOURCES_ALLOCATED": add("Request", d.request_id); add("Shelter", d.shelter); add("Distance", d.distance_km !== undefined ? d.distance_km + " km" : ""); add("Beds", d.beds_reserved); add("Supplies", (d.supplies || []).join(", ")); break;
    case "REQUEST_RESOLVED": add("Outcome", d.resolution); add("Shelter", d.shelter); break;
    default:
      Object.entries(d).slice(0, 6).forEach(([k, v]) => add(formatCategory(k), typeof v === "object" ? JSON.stringify(v) : v));
  }
  return parts.length ? parts.join(" &nbsp;·&nbsp; ") : escapeHtml(JSON.stringify(d));
}

async function loadAuditTrail() {
  try {
    const res = await fetch(`${API_BASE}/api/audit-trail`);
    const logs = await res.json();
    const tbody = document.getElementById("audit-tbody");
    if (!logs.length) {
      tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; color:#94a3b8;">No audit events recorded.</td></tr>`;
      return;
    }
    tbody.innerHTML = logs.map(l => {
      const timeStr = new Date(l.created_at * 1000).toLocaleString([], { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit" });
      const [label, color] = AUDIT_ACTIONS[l.action] || [formatCategory(String(l.action).toLowerCase()), "#38bdf8"];
      return `
        <tr>
          <td style="font-family:var(--font-mono); color:#94a3b8; white-space:nowrap;">${timeStr}</td>
          <td><span class="badge badge-sev-2">${formatCategory(l.entity_type)}</span></td>
          <td style="font-family:var(--font-mono);">${escapeHtml(l.entity_id)}</td>
          <td><strong style="color:${color};">${label}</strong></td>
          <td>${escapeHtml(l.performed_by || "System")}</td>
          <td class="audit-detail">${summarizeAudit(l)}</td>
        </tr>
      `;
    }).join("");
  } catch (err) {
    console.error("Error loading audit trail:", err);
  }
}

// 7. CITIZEN FORM SUBMIT
async function handleCitizenSubmit(e) {
  e.preventDefault();
  const payload = {
    contact_name: document.getElementById("form-name").value,
    contact_phone: document.getElementById("form-phone").value,
    address_text: document.getElementById("form-address").value,
    lat: parseFloat(document.getElementById("form-lat").value),
    lon: parseFloat(document.getElementById("form-lon").value),
    category: document.getElementById("form-category").value,
    severity_level: parseInt(document.getElementById("form-severity").value),
    vulnerability_level: parseInt(document.getElementById("form-vuln").value),
    water_level_meters: parseFloat(document.getElementById("form-water-level").value),
    people_count: parseInt(document.getElementById("form-people").value),
    infants_count: parseInt(document.getElementById("form-infants").value),
    elderly_count: parseInt(document.getElementById("form-elderly").value),
    medical_urgency_notes: document.getElementById("form-notes").value,
    is_privacy_masked: document.getElementById("form-privacy").checked
  };

  try {
    const res = await fetch(`${API_BASE}/api/requests`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const result = await res.json();
    if (result.status === "created") {
      alert(`Emergency Request #${result.request_id} successfully submitted!\nCalculated Priority Score: ${result.priority_score}`);
      document.getElementById("citizen-request-form").reset();
      fetchDashboardStats();
      loadRequests();
      switchTab("requests-tab");
    }
  } catch (err) {
    alert("Error submitting request: " + err.message);
  }
}

function updateFormScorePreview() {
  const sev = parseInt(document.getElementById("form-severity")?.value || 3);
  const vuln = parseInt(document.getElementById("form-vuln")?.value || 2);
  const water = parseFloat(document.getElementById("form-water-level")?.value || 0.5);

  const score = (0.50 * sev) + (0.30 * vuln) + (0.15 * Math.min(water, 3.0) * 1.667);
  const el = document.getElementById("preview-score");
  if (el) el.innerText = score.toFixed(2);
}

// 8. BATCH ALLOCATION ENGINE
async function runAllocationAlgorithm() {
  try {
    const res = await fetch(`${API_BASE}/api/algorithm/run-allocations`, { method: "POST" });
    const data = await res.json();
    alert(`Batch Allocation Engine Complete!\nAllocated: ${data.allocated_count}\nUnresolved / Constrained: ${data.unresolved_count}`);
    fetchDashboardStats();
    loadRequests();
    loadShelters();
    loadVolunteers();
    loadAuditTrail();
  } catch (err) {
    alert("Allocation error: " + err.message);
  }
}

// 9. ADVANCED FLOOD SIMULATION (100+ REQUESTS)
async function run100Simulation() {
  const container = document.getElementById("simulation-results-container");
  container.innerHTML = `<div style="text-align:center; padding:20px; color:#38bdf8;">⏳ Generating 120 synthetic flood requests across 3 zones and running constrained spatial allocations...</div>`;

  try {
    const res = await fetch(`${API_BASE}/api/simulation/run-100`, { method: "POST" });
    const data = await res.json();
    const val = data.validation_report;

    const isPassed = val.overall_status === "PASSED";

    container.innerHTML = `
      <div class="report-card">
        <div class="report-verdict ${isPassed ? 'passed' : 'failed'}">
          <span style="font-size:24px;">${isPassed ? '✅' : '⚠️'}</span>
          <div>
            <strong style="font-size:16px;">Validation Verdict: ${val.overall_status}</strong>
            <p style="margin:2px 0 0 0; font-size:13px;">${val.evaluation_verdict}</p>
          </div>
        </div>

        <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(200px, 1fr)); gap:12px; margin-bottom:16px;">
          <div class="stat-card">
            <span class="label">Duplicate Allocations</span>
            <span class="value" style="color:${val.integrity_checks.double_allocation_violations === 0 ? '#10b981' : '#ef4444'};">
              ${val.integrity_checks.double_allocation_violations}
            </span>
            <span class="sub">Target: 0 violations</span>
          </div>
          <div class="stat-card">
            <span class="label">High-Priority Resolution</span>
            <span class="value" style="color:#60a5fa;">${val.metrics.high_priority_resolution_percent}%</span>
            <span class="sub">Severity 4 & 5 fulfilled</span>
          </div>
          <div class="stat-card">
            <span class="label">Total Generated</span>
            <span class="value">${val.metrics.total_requests}</span>
            <span class="sub">synthetic requests</span>
          </div>
          <div class="stat-card">
            <span class="label">Total Fulfilled</span>
            <span class="value" style="color:#10b981;">${val.metrics.total_allocated}</span>
            <span class="sub">against resource limits</span>
          </div>
        </div>

        <h4 style="margin:12px 0 8px 0;">Severity Breakdown & Resolution Rate:</h4>
        <table style="margin-bottom:16px;">
          <thead>
            <tr><th>Severity Level</th><th>Total Incoming</th><th>Resolved / Allocated</th><th>Resolution Rate</th></tr>
          </thead>
          <tbody>
            ${val.metrics.severity_breakdown.map(s => `
              <tr>
                <td><span class="badge badge-sev-${s.severity_level}">Level ${s.severity_level}</span></td>
                <td>${s.total}</td>
                <td>${s.resolved}</td>
                <td><strong>${s.resolution_rate_percent}%</strong></td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      </div>
    `;

    fetchDashboardStats();
    loadRequests();
    loadShelters();
    loadVolunteers();
    loadAuditTrail();
  } catch (err) {
    container.innerHTML = `<div style="color:#ef4444; padding:20px;">Simulation error: ${err.message}</div>`;
  }
}

// 10. DYNAMIC SHELTER DISASTER SIMULATION
async function triggerShelterDisaster() {
  if (!confirm("Simulate dynamic crisis condition? This will simulate an emergency flood breach at East Riverfront Polytechnic, evacuating its occupants and triggering immediate re-allocation!")) return;

  try {
    const res = await fetch(`${API_BASE}/api/simulation/shelter-failure`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ shelter_id: "shl-east-academy", reason: "Direct flood breach into basement generator room" })
    });
    const data = await res.json();
    alert(`Dynamic Crisis Triggered!\nDisplaced Occupants: ${data.displaced_people}\nEvacuation Batches Created: ${data.evacuation_requests_created}\nRe-allocation initiated immediately.`);
    fetchDashboardStats();
    loadRequests();
    loadShelters();
    loadAuditTrail();
  } catch (err) {
    alert("Failure simulation error: " + err.message);
  }
}

// 11. RESET CRISIS
async function resetDatabaseCrisis() {
  if (!confirm("Reset database to baseline scenario? All synthetic simulation requests will be cleared.")) return;
  try {
    await fetch(`${API_BASE}/api/simulation/reset`, { method: "POST" });
    alert("Scenario reset to clean baseline.");
    fetchDashboardStats();
    loadRequests();
    loadShelters();
    loadVolunteers();
    loadAuditTrail();
  } catch (err) {
    alert("Reset error: " + err.message);
  }
}

// 12. EXPLAIN DECISION MODAL
function openExplainModal(reqId) {
  const req = cachedRequests.find(r => r.id === reqId);
  if (!req || !req.decision_rationale) {
    alert("No decision rationale logged for this request yet.");
    return;
  }

  const rat = req.decision_rationale;
  const modal = document.getElementById("explain-modal");
  const body = document.getElementById("explain-modal-body");

  body.innerHTML = `
    <div style="margin-bottom:12px;">
      <p style="font-size:14px; color:#e2e8f0; margin-bottom:8px;">${escapeHtml(rat.explanation_text || '')}</p>
    </div>
    <div style="background:#090d16; padding:12px; border-radius:6px; margin-bottom:12px; border:1px solid #334155;">
      <div style="font-size:12px; color:#94a3b8; margin-bottom:6px;">MATHEMATICAL MULTI-CRITERIA SCORING:</div>
      <div style="display:flex; justify-content:space-between; font-size:13px;">
        <span>Final Priority Score: <strong style="color:#f59e0b;">${rat.priority_score}</strong></span>
        <span>Distance to Shelter: <strong style="color:#60a5fa;">${rat.matched_shelter?.distance_km?.toFixed(2)} km</strong></span>
      </div>
    </div>
    <div style="margin-bottom:12px;">
      <h4 style="font-size:13px; color:#94a3b8; margin-bottom:6px;">ALTERNATIVES EVALUATED & TRADEOFFS:</h4>
      ${(rat.alternatives_evaluated || []).map(alt => `
        <div style="font-size:12px; padding:6px 8px; background:#1e293b; border-radius:4px; margin-bottom:4px; display:flex; justify-content:space-between;">
          <span>${escapeHtml(alt.shelter_name)} (${alt.distance_km?.toFixed(2)} km)</span>
          <span style="color:${alt.decision === 'SELECTED' ? '#10b981' : '#ef4444'};"><strong>${alt.decision}</strong>: ${escapeHtml(alt.reason)}</span>
        </div>
      `).join("")}
    </div>
    <pre class="json-view">${escapeHtml(JSON.stringify(rat, null, 2))}</pre>
  `;

  modal.classList.add("open");
}

function openCapacityModal(shelterId, currentOcc) {
  document.getElementById("cap-shelter-id").value = shelterId;
  document.getElementById("cap-new-occupancy").value = currentOcc;
  document.getElementById("capacity-modal").classList.add("open");
}

async function handleCapacityUpdate(e) {
  e.preventDefault();
  const shelterId = document.getElementById("cap-shelter-id").value;
  const newOcc = parseInt(document.getElementById("cap-new-occupancy").value);
  const reason = document.getElementById("cap-reason").value;

  try {
    const res = await fetch(`${API_BASE}/api/shelters/${shelterId}/capacity`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ current_occupancy: newOcc, reason: reason })
    });
    const result = await res.json();
    if (result.status === "updated") {
      closeModal("capacity-modal");
      loadShelters();
      fetchDashboardStats();
    } else {
      alert("Error: " + (result.error || "Update failed"));
    }
  } catch (err) {
    alert("Error updating capacity: " + err.message);
  }
}

function openVolunteerModal() {
  document.getElementById("volunteer-modal").classList.add("open");
}

async function handleVolunteerRegister(e) {
  e.preventDefault();
  const name = document.getElementById("vol-name").value;
  const phone = document.getElementById("vol-phone").value;
  const checkedBoxes = document.querySelectorAll('input[name="vol-skills"]:checked');
  const skills = Array.from(checkedBoxes).map(cb => cb.value);

  try {
    const res = await fetch(`${API_BASE}/api/volunteers`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ full_name: name, phone: phone, skills: skills })
    });
    const result = await res.json();
    if (result.status === "created") {
      closeModal("volunteer-modal");
      alert("Volunteer registered successfully!");
      loadVolunteers();
      fetchDashboardStats();
    }
  } catch (err) {
    alert("Error registering volunteer: " + err.message);
  }
}

async function verifyRequest(reqId, action) {
  try {
    const res = await fetch(`${API_BASE}/api/requests/${reqId}/verify`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: action, notes: `Manual ${action} by relief coordinator` })
    });
    const result = await res.json();
    loadRequests();
    fetchDashboardStats();
  } catch (err) {
    alert("Error verifying request: " + err.message);
  }
}

async function restockItem(itemId) {
  try {
    await fetch(`${API_BASE}/api/inventory/${itemId}/restock`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ quantity: 100 })
    });
    loadShelters();
  } catch (err) {
    alert("Error restocking: " + err.message);
  }
}

function closeModal(modalId) {
  document.getElementById(modalId)?.classList.remove("open");
}

function formatCategory(cat) {
  if (!cat) return "";
  return cat.split('_').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
}

function escapeHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}
