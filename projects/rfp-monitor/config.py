"""
config.py -- the only file you should need to edit.

Change keywords or switch sources off here, then restart the app
(close its window and double-click "Start RFP Monitor.bat").
"""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "rfp_monitor.db"
LOG_PATH = BASE_DIR / "data" / "monitor.log"

# Checks only run when you press Refresh in the dashboard (or run
# `python monitor.py`). Nothing happens on a timer.

# ReliefWeb needs a free approved appname before its source will run:
# request one at apidoc.reliefweb.int/parameters#appname (takes a minute),
# then paste it here, e.g. RELIEFWEB_APPNAME = "haider-rfp-monitor".
RELIEFWEB_APPNAME = ""

# Don't store notices whose deadline has already passed.
SKIP_EXPIRED = True
# World Bank notices with no deadline (General Procurement Notices) older
# than this are skipped -- keyword searches otherwise surface years-old ones.
MAX_AGE_DAYS_NO_DEADLINE = 120

# Windows pop-up when a check finds new notices scoring at least this much.
# Set to None to turn notifications off.
NOTIFY_MIN_SCORE = 40

# Turn a source off by its function name in sources.py, e.g. {"fetch_gcf"}.
DISABLED_SOURCES: set[str] = set()
if not RELIEFWEB_APPNAME:
    DISABLED_SOURCES.add("fetch_reliefweb")  # stays off until the appname above is set

REQUEST_TIMEOUT = 45  # seconds per HTTP request

# A notice is kept only if its title or detail contains at least one of these.
# Matching ignores case and accents, and a term also matches longer words
# ("flood" matches "flooding" and "floodplain").
KEYWORDS = [
    # Urban planning / cities
    "urban planning", "urban resilience", "urban development", "city resilience",
    "land use planning", "regional planning", "urban infrastructure",
    "informal settlements", "housing resilience", "spatial planning",
    "smart city", "urban regeneration",
    # Disaster risk reduction / hazard mitigation (general)
    "disaster risk", "disaster risk reduction", "hazard mitigation",
    "risk reduction", "disaster preparedness", "preparedness",
    "vulnerability assessment", "early warning", "multi-hazard",
    "hazard mapping", "damage assessment", "recovery planning",
    # Specific disaster / hazard types, standalone
    "earthquake", "seismic", "flood", "flooding", "flash flood",
    "hurricane", "cyclone", "typhoon", "tsunami", "landslide",
    "wildfire", "bushfire", "drought", "heatwave", "storm surge",
    "volcanic", "extreme weather",
    # Adaptation / resilience / mitigation, standalone
    "adaptation", "resilience", "resilient", "mitigation",
    "climate adaptation", "climate resilience", "climate risk",
    # Response and recovery
    "disaster response", "emergency response", "post-disaster recovery",
    "disaster recovery", "emergency management", "humanitarian response",
    "recovery and reconstruction",
    # Nature-based / ecosystem approaches
    "nature-based solutions", "nature-based", "ecosystem-based adaptation",
    "green infrastructure", "ecosystem restoration",
    # Sustainability
    "sustainability", "sustainable development", "sustainable urban development",
    # Community / governance
    "community resilience", "social vulnerability", "disaster governance",
    "climate justice", "community engagement",
    # French / Spanish equivalents (many AfDB, UNGM and UNDP notices aren't in English)
    "inondation", "sécheresse", "séisme", "gestion des risques", "catastrophe",
    "inundación", "sequía", "riesgo de desastres", "gestión del riesgo", "desastres",
]

# Generic terms that also show up in off-topic notices ("fraud mitigation",
# "IT resilience"). They still count as a match but score lower than the
# field-specific terms (everything in KEYWORDS that is not listed here).
BROAD_KEYWORDS = [
    "resilience", "resilient", "adaptation", "mitigation", "sustainability",
    "sustainable development", "sustainable urban development", "preparedness",
    "community engagement", "urban development", "urban infrastructure",
    "regional planning", "smart city", "urban regeneration", "green infrastructure",
    "ecosystem restoration", "nature-based", "climate justice", "catastrophe",
    "desastres",
]

# Relevance hints, checked against title + notice type: work a consultant or
# researcher bids on scores higher, purchases of goods and works lower.
SERVICE_HINTS = [
    "consult", "technical assistance", "advisory", "study", "studies", "assessment",
    "evaluation", "research", "expression of interest", "eoi", "capacity building",
    "training", "strategy", "planning", "supervision", "analytics", "mapping",
    "survey", "guideline", "policy", "grant",
]
GOODS_HINTS = [
    "supply", "supplies", "delivery of", "purchase of", "acquisition of", "equipment",
    "vehicle", "motorcycle", "laptop", "computer", "furniture", "stationery",
    "printing", "catering", "fuel", "cleaning", "security guard", "insurance",
    "civil works", "construction works", "spare parts",
]
