-- ============================================================================
-- ReliefConnect: PostgreSQL + PostGIS Production Schema
-- Designed for the ReliefConnect Disaster Relief Coordination Prototype
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "postgis";

-- 1. USERS & ROLES
CREATE TYPE user_role AS ENUM (
    'citizen',
    'relief_coordinator',
    'shelter_manager',
    'volunteer',
    'administrator'
);

CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    full_name VARCHAR(120) NOT NULL,
    phone_number VARCHAR(30) NOT NULL,
    email VARCHAR(120) UNIQUE,
    role user_role NOT NULL DEFAULT 'citizen',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 2. INCIDENTS (e.g. "Monsoon Flood 2026 - Zone East")
CREATE TYPE incident_status AS ENUM ('monitoring', 'active', 'contained', 'closed');

CREATE TABLE incidents (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(150) NOT NULL,
    description TEXT,
    severity_level INT CHECK (severity_level BETWEEN 1 AND 5),
    status incident_status NOT NULL DEFAULT 'active',
    boundary_area GEOGRAPHY(Polygon, 4326),
    declared_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at TIMESTAMPTZ
);

-- 3. LOCATIONS (Geospatial with PostGIS & Privacy Masking)
CREATE TABLE locations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    point_geom GEOGRAPHY(Point, 4326) NOT NULL,
    address_text TEXT NOT NULL,
    district VARCHAR(100),
    landmark VARCHAR(150),
    is_privacy_masked BOOLEAN NOT NULL DEFAULT FALSE,
    masked_point_geom GEOGRAPHY(Point, 4326),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_locations_geom ON locations USING GIST(point_geom);

-- 4. SHELTERS
CREATE TYPE shelter_status AS ENUM ('open', 'at_capacity', 'evacuating', 'closed');

CREATE TABLE shelters (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    incident_id UUID REFERENCES incidents(id) ON DELETE SET NULL,
    manager_id UUID REFERENCES users(id) ON DELETE SET NULL,
    name VARCHAR(150) NOT NULL,
    location_id UUID NOT NULL REFERENCES locations(id) ON DELETE RESTRICT,
    total_capacity INT NOT NULL CHECK (total_capacity >= 0),
    current_occupancy INT NOT NULL DEFAULT 0 CHECK (current_occupancy >= 0),
    status shelter_status NOT NULL DEFAULT 'open',
    contact_phone VARCHAR(30),
    has_medical_unit BOOLEAN DEFAULT FALSE,
    has_accessible_access BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_capacity CHECK (current_occupancy <= total_capacity)
);

-- 5. CAPACITY UPDATES (Audit log of capacity changes)
CREATE TABLE capacity_updates (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    shelter_id UUID NOT NULL REFERENCES shelters(id) ON DELETE CASCADE,
    previous_occupancy INT NOT NULL,
    new_occupancy INT NOT NULL,
    delta_change INT NOT NULL,
    reason TEXT,
    updated_by UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_capacity_updates_shelter ON capacity_updates(shelter_id, created_at DESC);

-- 6. RELIEF INVENTORY ITEMS
CREATE TYPE inventory_category AS ENUM (
    'food_rations',
    'drinking_water',
    'medical_supplies',
    'clothing_blankets',
    'rescue_equipment',
    'sanitation_hygiene'
);

CREATE TABLE inventory_items (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    shelter_id UUID NOT NULL REFERENCES shelters(id) ON DELETE CASCADE,
    category inventory_category NOT NULL,
    item_name VARCHAR(120) NOT NULL,
    quantity_available INT NOT NULL DEFAULT 0 CHECK (quantity_available >= 0),
    quantity_reserved INT NOT NULL DEFAULT 0 CHECK (quantity_reserved >= 0),
    unit VARCHAR(30) NOT NULL DEFAULT 'units',
    minimum_threshold INT DEFAULT 10,
    last_restocked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_inventory_balance CHECK (quantity_available >= quantity_reserved)
);
CREATE INDEX idx_inventory_shelter ON inventory_items(shelter_id, category);

-- 7. HELP REQUESTS (Core Citizens & Coordinators entity)
CREATE TYPE request_category AS ENUM (
    'rescue_evacuation',
    'medical_emergency',
    'food_and_water',
    'shelter_need',
    'clothing_blankets',
    'other'
);

CREATE TYPE request_status AS ENUM (
    'submitted',
    'verified',
    'allocated',
    'in_progress',
    'dispatched',
    'resolved',
    'rejected'
);

CREATE TABLE help_requests (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    incident_id UUID REFERENCES incidents(id) ON DELETE SET NULL,
    requester_id UUID REFERENCES users(id) ON DELETE SET NULL,
    contact_name VARCHAR(120) NOT NULL,
    contact_phone VARCHAR(30) NOT NULL,
    location_id UUID NOT NULL REFERENCES locations(id) ON DELETE RESTRICT,
    category request_category NOT NULL,
    severity_level INT NOT NULL CHECK (severity_level BETWEEN 1 AND 5),
    vulnerability_level INT NOT NULL DEFAULT 1 CHECK (vulnerability_level BETWEEN 1 AND 5),
    people_count INT NOT NULL DEFAULT 1 CHECK (people_count >= 1),
    infants_count INT NOT NULL DEFAULT 0,
    elderly_count INT NOT NULL DEFAULT 0,
    medical_urgency_notes TEXT,
    water_level_meters NUMERIC(4,2) DEFAULT 0.0,
    calculated_priority_score NUMERIC(6,3) DEFAULT 0.0,
    status request_status NOT NULL DEFAULT 'submitted',
    verified_by UUID REFERENCES users(id) ON DELETE SET NULL,
    verified_at TIMESTAMPTZ,
    resolved_at TIMESTAMPTZ,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_requests_status_priority ON help_requests(status, calculated_priority_score DESC);

-- 8. VOLUNTEERS
CREATE TYPE volunteer_status AS ENUM ('available', 'assigned', 'off_duty');

CREATE TABLE volunteers (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    skills TEXT[] NOT NULL DEFAULT '{}', -- e.g. ARRAY['boat_rescue', 'first_aid', 'heavy_machinery']
    current_location_id UUID REFERENCES locations(id) ON DELETE SET NULL,
    status volunteer_status NOT NULL DEFAULT 'available',
    emergency_contact VARCHAR(100),
    rating NUMERIC(3,2) DEFAULT 5.0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 9. RESOURCE ALLOCATIONS (With atomic reservations and explainability trail)
CREATE TYPE allocation_status AS ENUM ('reserved', 'dispatched', 'delivered', 'cancelled');

CREATE TABLE allocations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    help_request_id UUID NOT NULL REFERENCES help_requests(id) ON DELETE RESTRICT,
    shelter_id UUID REFERENCES shelters(id) ON DELETE RESTRICT,
    inventory_item_id UUID REFERENCES inventory_items(id) ON DELETE RESTRICT,
    quantity_allocated INT NOT NULL DEFAULT 1 CHECK (quantity_allocated > 0),
    beds_allocated INT NOT NULL DEFAULT 0 CHECK (beds_allocated >= 0),
    status allocation_status NOT NULL DEFAULT 'reserved',
    allocated_by_algorithm BOOLEAN NOT NULL DEFAULT TRUE,
    decision_rationale JSONB NOT NULL DEFAULT '{}'::jsonb, -- records why this allocation was picked
    allocated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    dispatched_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ
);
CREATE INDEX idx_allocations_request ON allocations(help_request_id);
CREATE INDEX idx_allocations_shelter ON allocations(shelter_id);

-- 10. ASSIGNMENT HISTORY (Volunteer task history & handovers)
CREATE TABLE assignment_history (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    volunteer_id UUID NOT NULL REFERENCES volunteers(id) ON DELETE CASCADE,
    help_request_id UUID NOT NULL REFERENCES help_requests(id) ON DELETE CASCADE,
    assigned_by UUID REFERENCES users(id) ON DELETE SET NULL,
    instructions TEXT,
    assigned_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    handover_notes TEXT,
    audit_trail JSONB NOT NULL DEFAULT '[]'::jsonb
);
CREATE INDEX idx_assignment_volunteer ON assignment_history(volunteer_id);
CREATE INDEX idx_assignment_request ON assignment_history(help_request_id);

-- Trigger: Update updated_at timestamp
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_shelters_updated BEFORE UPDATE ON shelters FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_requests_updated BEFORE UPDATE ON help_requests FOR EACH ROW EXECUTE FUNCTION set_updated_at();
