"""
ReliefConnect Domain Models & Types
Disaster Relief Coordination Prototype
"""

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import List, Optional, Dict, Any
import time
import math

class UserRole(str, Enum):
    CITIZEN = "citizen"
    RELIEF_COORDINATOR = "relief_coordinator"
    SHELTER_MANAGER = "shelter_manager"
    VOLUNTEER = "volunteer"
    ADMINISTRATOR = "administrator"

class IncidentStatus(str, Enum):
    MONITORING = "monitoring"
    ACTIVE = "active"
    CONTAINED = "contained"
    CLOSED = "closed"

class RequestCategory(str, Enum):
    RESCUE_EVACUATION = "rescue_evacuation"
    MEDICAL_EMERGENCY = "medical_emergency"
    FOOD_AND_WATER = "food_and_water"
    SHELTER_NEED = "shelter_need"
    CLOTHING_BLANKETS = "clothing_blankets"
    OTHER = "other"

class RequestStatus(str, Enum):
    SUBMITTED = "submitted"
    VERIFIED = "verified"
    ALLOCATED = "allocated"
    IN_PROGRESS = "in_progress"
    DISPATCHED = "dispatched"
    RESOLVED = "resolved"
    REJECTED = "rejected"

class ShelterStatus(str, Enum):
    OPEN = "open"
    AT_CAPACITY = "at_capacity"
    EVACUATING = "evacuating"
    CLOSED = "closed"

class InventoryCategory(str, Enum):
    FOOD_RATIONS = "food_rations"
    DRINKING_WATER = "drinking_water"
    MEDICAL_SUPPLIES = "medical_supplies"
    CLOTHING_BLANKETS = "clothing_blankets"
    RESCUE_EQUIPMENT = "rescue_equipment"
    SANITATION_HYGIENE = "sanitation_hygiene"

class AllocationStatus(str, Enum):
    RESERVED = "reserved"
    DISPATCHED = "dispatched"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"

class VolunteerStatus(str, Enum):
    AVAILABLE = "available"
    ASSIGNED = "assigned"
    OFF_DUTY = "off_duty"


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate great-circle distance between two points on Earth in kilometers."""
    R = 6371.0  # Earth radius in kilometers
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return round(R * c, 2)
