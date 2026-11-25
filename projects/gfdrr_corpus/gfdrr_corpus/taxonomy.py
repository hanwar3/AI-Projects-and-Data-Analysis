"""Official taxonomy aligned with GFDRR Strategy 2026-2030 and World Bank AMS 6.21A classification."""

from __future__ import annotations

PRIORITY_AREAS: tuple[str, ...] = (
    "Priority 1: Strengthening Risk-Informed Decision Making",
    "Priority 2: Reducing Risk and Mainstreaming DRM",
    "Priority 3: Enhancing Financial Preparedness and Disaster Risk Finance",
    "Priority 4: Improving Disaster Preparedness and Resilient Recovery",
)

THEMATIC_TO_PRIORITY: dict[str, str] = {
    "Digital Earth": PRIORITY_AREAS[0],
    "Disaster Risk Analytics": PRIORITY_AREAS[0],
    "Building Regulation for Resilience - Resilient Housing": PRIORITY_AREAS[1],
    "City Resilience - Urban Heat Resilience": PRIORITY_AREAS[1],
    "Nature-based Solutions for Climate Resilience": PRIORITY_AREAS[1],
    "Resilient Infrastructure": PRIORITY_AREAS[1],
    "Resilient Human Development Infra - Safer Schools & Health Systems": PRIORITY_AREAS[1],
    "Disaster Risk Finance": PRIORITY_AREAS[2],
    "Emergency Preparedness and Response": PRIORITY_AREAS[3],
    "Hydromet Services and Early Warning Systems": PRIORITY_AREAS[3],
}

THEMATIC_AREAS: tuple[str, ...] = tuple(THEMATIC_TO_PRIORITY.keys())

CROSSCUTTING_AREAS: tuple[str, ...] = (
    "FCV",
    "Gender/Inclusion",
)

WB_REGIONS: tuple[str, ...] = (
    "SAR",  # South Asia
    "AFR",  # Sub-Saharan Africa
    "EAP",  # East Asia and Pacific
    "LAC",  # Latin America and the Caribbean
    "ECA",  # Europe and Central Asia
    "MNA",  # Middle East and North Africa
)

# World Bank Access to Information & AMS 6.21A 4-tier classification hierarchy
WB_CLASSIFICATIONS: tuple[str, ...] = (
    "Public",
    "Official Use Only",
    "Confidential",
    "Strictly Confidential",
)

WB_CLASSIFICATION_ORDER: dict[str, int] = {
    tier: idx for idx, tier in enumerate(WB_CLASSIFICATIONS)
}

CLEARANCE_ROLES: tuple[str, ...] = (
    "public_external",
    "wbg_analyst",
    "me_specialist",
    "restricted_audit",
)

CLEARANCE_MAX_TIER: dict[str, str] = {
    "public_external": "Public",
    "wbg_analyst": "Official Use Only",
    "me_specialist": "Confidential",
    "restricted_audit": "Strictly Confidential",
}
