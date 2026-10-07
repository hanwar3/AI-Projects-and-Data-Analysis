"""Regions - where in the world you are searching.

A region bundles everything that changes when you move the search from
Washington to London to Dubai:

* the location strings sent to each source
* the ISO country code Indeed and Adzuna need
* the metro/city words that count as "right here" when scoring location
* which sources are worth running at all (USAJOBS is meaningless outside the
  US; Arbeitnow is meaningless outside Europe)

Presets live here so they are always available. A profile can override any
field of a preset, or define a region of its own, under a ``regions:`` key.
Switch with ``--region <key>`` or by setting ``active_region`` in the profile.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Sources that exist. A region lists the subset worth running.
ALL_SOURCES = [
    "ats", "usajobs", "reliefweb", "adzuna", "remotive", "jobicy",
    "arbeitnow", "themuse", "linkedin", "indeed",
]

# Free and keyless, so they are safe to leave on everywhere.
KEYLESS = {"ats", "remotive", "jobicy", "arbeitnow", "themuse"}


@dataclass
class Region:
    key: str
    label: str
    # Locations offered to sources that take a free-text location, and used
    # for location scoring. Order matters: earlier entries score higher.
    locations: list[str] = field(default_factory=list)
    # City/metro words that mean "exactly where I want to be".
    metro_terms: list[str] = field(default_factory=list)
    # ISO-3166 alpha-2, lowercase. Indeed and Adzuna both need one.
    country: str = ""
    # Adzuna only serves these countries; blank means Adzuna is skipped.
    adzuna_country: str = ""
    # LinkedIn matches its own location strings, which are wordier.
    linkedin_location: str = ""
    # ReliefWeb country facet name, for the humanitarian source.
    reliefweb_country: str = ""
    sources: list[str] = field(default_factory=list)
    remote_ok: bool = True
    notes: str = ""

    def enabled(self, source: str) -> bool:
        return source in self.sources

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key, "label": self.label, "locations": self.locations,
            "metro_terms": self.metro_terms, "country": self.country,
            "adzuna_country": self.adzuna_country,
            "linkedin_location": self.linkedin_location,
            "reliefweb_country": self.reliefweb_country,
            "sources": self.sources, "remote_ok": self.remote_ok,
            "notes": self.notes,
        }


def _r(**kw: Any) -> Region:
    kw.setdefault("sources", list(ALL_SOURCES))
    return Region(**kw)


# --------------------------------------------------------------- the presets
PRESETS: dict[str, Region] = {
    "us_dc": _r(
        key="us_dc",
        label="United States - DC metro (Washington / Virginia / Maryland)",
        locations=["Washington, DC", "Virginia", "Maryland", "Remote"],
        metro_terms=[
            "washington", "district of columbia", "arlington", "alexandria",
            "reston", "fairfax", "bethesda", "rockville", "mclean", "tysons",
            "silver spring", "college park", "vienna va", "herndon", "falls church",
        ],
        country="us", adzuna_country="us",
        linkedin_location="Washington, District of Columbia, United States",
        reliefweb_country="United States of America",
        sources=["ats", "usajobs", "reliefweb", "adzuna", "remotive", "jobicy",
                 "themuse", "linkedin", "indeed"],
        notes="Densest market for federal hazards work: FEMA, DHS, NOAA, HUD, World Bank.",
    ),
    "us": _r(
        key="us", label="United States - nationwide",
        locations=["United States", "Remote"],
        metro_terms=["united states", "usa", "nationwide"],
        country="us", adzuna_country="us",
        linkedin_location="United States",
        reliefweb_country="United States of America",
        sources=["ats", "usajobs", "reliefweb", "adzuna", "remotive", "jobicy",
                 "themuse", "linkedin", "indeed"],
    ),
    "us_texas": _r(
        key="us_texas", label="United States - Texas",
        locations=["Texas", "Austin, TX", "Houston, TX", "College Station, TX", "Remote"],
        metro_terms=["texas", "austin", "houston", "dallas", "san antonio",
                     "college station", "fort worth"],
        country="us", adzuna_country="us",
        linkedin_location="Texas, United States",
        reliefweb_country="United States of America",
        sources=["ats", "usajobs", "adzuna", "remotive", "jobicy", "themuse",
                 "linkedin", "indeed"],
    ),
    "canada": _r(
        key="canada", label="Canada",
        locations=["Canada", "Ottawa, ON", "Toronto, ON", "Vancouver, BC", "Remote"],
        metro_terms=["canada", "ottawa", "toronto", "vancouver", "montreal",
                     "calgary", "ontario", "british columbia", "quebec"],
        country="ca", adzuna_country="ca",
        linkedin_location="Canada", reliefweb_country="Canada",
        sources=["ats", "reliefweb", "adzuna", "remotive", "jobicy", "themuse",
                 "linkedin", "indeed"],
    ),
    "uk": _r(
        key="uk", label="United Kingdom",
        locations=["United Kingdom", "London", "Remote"],
        metro_terms=["united kingdom", "london", "manchester", "edinburgh",
                     "birmingham", "bristol", "glasgow", "oxford", "cambridge"],
        country="gb", adzuna_country="gb",
        linkedin_location="United Kingdom",
        reliefweb_country="United Kingdom of Great Britain and Northern Ireland",
        sources=["ats", "reliefweb", "adzuna", "remotive", "jobicy", "arbeitnow",
                 "themuse", "linkedin", "indeed"],
        notes="Strong for DRR and climate policy: DFID successors, ODI, IIED, Red Cross.",
    ),
    "europe": _r(
        key="europe", label="Europe (EU + Switzerland + Norway)",
        locations=["Europe", "Netherlands", "Germany", "Switzerland", "Belgium", "Remote"],
        metro_terms=["europe", "netherlands", "germany", "berlin", "amsterdam",
                     "the hague", "geneva", "zurich", "brussels", "copenhagen",
                     "stockholm", "vienna", "paris", "rome", "madrid", "bonn"],
        country="nl", adzuna_country="nl",
        linkedin_location="European Union",
        reliefweb_country="Switzerland",
        sources=["ats", "reliefweb", "adzuna", "remotive", "jobicy", "arbeitnow",
                 "themuse", "linkedin", "indeed"],
        notes="Geneva, The Hague and Bonn concentrate UN and DRR institutions.",
    ),
    "australia_nz": _r(
        key="australia_nz", label="Australia and New Zealand",
        locations=["Australia", "New Zealand", "Sydney", "Melbourne", "Remote"],
        metro_terms=["australia", "new zealand", "sydney", "melbourne", "canberra",
                     "brisbane", "perth", "auckland", "wellington"],
        country="au", adzuna_country="au",
        linkedin_location="Australia", reliefweb_country="Australia",
        sources=["ats", "reliefweb", "adzuna", "remotive", "jobicy", "themuse",
                 "linkedin", "indeed"],
    ),
    "gulf": _r(
        key="gulf", label="Gulf states (UAE, Saudi Arabia, Qatar)",
        locations=["United Arab Emirates", "Dubai", "Abu Dhabi", "Saudi Arabia", "Qatar"],
        metro_terms=["united arab emirates", "uae", "dubai", "abu dhabi",
                     "saudi arabia", "riyadh", "jeddah", "qatar", "doha",
                     "kuwait", "bahrain", "oman", "muscat"],
        country="ae", adzuna_country="",
        linkedin_location="United Arab Emirates",
        reliefweb_country="United Arab Emirates",
        sources=["ats", "reliefweb", "remotive", "jobicy", "linkedin", "indeed"],
        notes="Adzuna does not cover the Gulf; LinkedIn and Indeed carry it.",
    ),
    "south_asia": _r(
        key="south_asia", label="South Asia (Pakistan, India, Bangladesh, Nepal)",
        locations=["Pakistan", "Islamabad", "Lahore", "India", "Bangladesh", "Nepal"],
        metro_terms=["pakistan", "islamabad", "lahore", "karachi", "india",
                     "delhi", "new delhi", "mumbai", "bangladesh", "dhaka",
                     "nepal", "kathmandu", "sri lanka", "colombo"],
        country="pk", adzuna_country="in",
        linkedin_location="Pakistan", reliefweb_country="Pakistan",
        sources=["ats", "reliefweb", "remotive", "jobicy", "linkedin", "indeed"],
        notes="ReliefWeb is the main source here - most DRR roles are UN or INGO.",
    ),
    "africa": _r(
        key="africa", label="Africa",
        locations=["Kenya", "Nairobi", "South Africa", "Nigeria", "Ethiopia", "Senegal"],
        metro_terms=["kenya", "nairobi", "south africa", "johannesburg", "cape town",
                     "nigeria", "abuja", "lagos", "ethiopia", "addis ababa",
                     "senegal", "dakar", "ghana", "accra", "uganda", "kampala"],
        country="za", adzuna_country="za",
        linkedin_location="Africa", reliefweb_country="Kenya",
        sources=["ats", "reliefweb", "adzuna", "remotive", "jobicy", "linkedin"],
        notes="Nairobi and Dakar are the regional humanitarian hubs.",
    ),
    "global_remote": _r(
        key="global_remote", label="Remote - anywhere in the world",
        locations=["Remote", "Anywhere", "Worldwide"],
        metro_terms=["remote", "anywhere", "worldwide", "global", "distributed"],
        country="us", adzuna_country="",
        linkedin_location="Worldwide", reliefweb_country="",
        sources=["ats", "reliefweb", "remotive", "jobicy", "arbeitnow", "themuse",
                 "linkedin"],
        notes="Remote-first boards only. No Indeed - it needs a country anchor.",
    ),
    "humanitarian_global": _r(
        key="humanitarian_global",
        label="Humanitarian and development - worldwide",
        locations=["Global", "Remote", "Geneva", "New York", "Nairobi", "Bangkok"],
        metro_terms=["global", "worldwide", "geneva", "new york", "nairobi",
                     "bangkok", "rome", "vienna", "copenhagen", "amman", "dakar"],
        country="us", adzuna_country="",
        linkedin_location="Worldwide", reliefweb_country="",
        sources=["reliefweb", "ats", "remotive", "jobicy", "linkedin"],
        notes=("A field rather than a place: UN agencies, INGOs and donors "
               "hiring for disaster risk, recovery and resilience anywhere. "
               "ReliefWeb carries almost all of it."),
    ),
}

DEFAULT_REGION = "us_dc"


def get(key: str, overrides: dict[str, Any] | None = None) -> Region:
    """Resolve a region by key, applying any profile overrides.

    A profile may redefine a preset field-by-field, or define a brand new
    region by supplying at least ``label``.
    """
    overrides = overrides or {}
    custom = overrides.get(key)

    if key in PRESETS:
        base = PRESETS[key].as_dict()
    elif custom:
        base = Region(key=key, label=key).as_dict()
        base["sources"] = list(ALL_SOURCES)
    else:
        raise KeyError(
            f"Unknown region {key!r}. Known: {', '.join(sorted(PRESETS))}. "
            f"Define your own under 'regions:' in the profile."
        )

    if custom:
        for field_name, value in custom.items():
            if field_name in base and value is not None:
                base[field_name] = value
    base["key"] = key
    return Region(**base)


def available(overrides: dict[str, Any] | None = None) -> list[Region]:
    keys = set(PRESETS) | set(overrides or {})
    return [get(k, overrides) for k in sorted(keys)]
