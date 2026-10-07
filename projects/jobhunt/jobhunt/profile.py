"""Your search profile: who you are, what you want, what disqualifies a job.

The profile is a single YAML file you edit by hand (``profiles/<name>.yaml``).
It drives three things at once:

1. **Which queries get run** against each source (``titles`` clusters).
2. **How a posting is scored** (``skills``, ``domains``, ``seniority``).
3. **What gets filtered out** (``exclude``, ``dealbreakers``).

``load_resume_text()`` pulls plain text out of a PDF/DOCX/TXT resume so the
scorer can compare a posting against what you have actually done, and so
``jobhunt prep`` can quote real bullets back at you.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

APP_ROOT = Path(__file__).resolve().parent.parent
PROFILE_DIR = APP_ROOT / "profiles"


@dataclass
class Profile:
    name: str = ""
    email: str = ""
    phone: str = ""
    linkedin: str = ""
    location: str = ""
    resume_path: str = ""

    # search
    titles: dict[str, list[str]] = field(default_factory=dict)
    locations: list[str] = field(default_factory=list)
    remote_ok: bool = True

    # scoring
    skills: dict[str, list[str]] = field(default_factory=dict)
    domains: list[str] = field(default_factory=list)
    seniority: list[str] = field(default_factory=list)
    bonus_terms: list[str] = field(default_factory=list)

    # filtering
    exclude_titles: list[str] = field(default_factory=list)
    exclude_companies: list[str] = field(default_factory=list)
    dealbreakers: dict[str, bool] = field(default_factory=dict)
    min_salary: float | None = None

    # sources
    sources: dict[str, Any] = field(default_factory=dict)
    watchlist: list[dict[str, str]] = field(default_factory=list)

    # region
    active_region: str = "us_dc"
    region_overrides: dict[str, Any] = field(default_factory=dict)

    # free text
    summary: str = ""
    raw: dict[str, Any] = field(default_factory=dict)
    _resume_text: str = ""

    # ------------------------------------------------------------------ load
    @classmethod
    def load(cls, path: str | Path) -> "Profile":
        path = Path(path)
        if not path.exists() and not path.is_absolute():
            candidate = PROFILE_DIR / path.name
            if candidate.exists():
                path = candidate
            elif (PROFILE_DIR / f"{path.stem}.yaml").exists():
                path = PROFILE_DIR / f"{path.stem}.yaml"
        if not path.exists():
            raise FileNotFoundError(f"No profile at {path}")

        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        p = cls(
            name=data.get("name", ""),
            email=data.get("email", ""),
            phone=data.get("phone", ""),
            linkedin=data.get("linkedin", ""),
            location=data.get("location", ""),
            resume_path=data.get("resume_path", ""),
            titles=data.get("titles", {}) or {},
            locations=data.get("locations", []) or [],
            remote_ok=bool(data.get("remote_ok", True)),
            skills=data.get("skills", {}) or {},
            domains=data.get("domains", []) or [],
            seniority=data.get("seniority", []) or [],
            bonus_terms=data.get("bonus_terms", []) or [],
            exclude_titles=data.get("exclude_titles", []) or [],
            exclude_companies=data.get("exclude_companies", []) or [],
            dealbreakers=data.get("dealbreakers", {}) or {},
            min_salary=data.get("min_salary"),
            sources=data.get("sources", {}) or {},
            watchlist=data.get("watchlist", []) or [],
            summary=data.get("summary", ""),
            active_region=data.get("active_region", "us_dc"),
            region_overrides=data.get("regions", {}) or {},
            raw=data,
        )
        p.path = path  # type: ignore[attr-defined]
        return p

    # ---------------------------------------------------------------- region
    def region(self, key: str | None = None):
        """The active :class:`~jobhunt.regions.Region`, or a named one.

        Imported lazily so ``regions`` can describe profiles without importing
        this module back.
        """
        from . import regions

        return regions.get(key or self.active_region, self.region_overrides)

    def search_locations(self, key: str | None = None) -> list[str]:
        """Locations to search: the region's, unless the profile overrides.

        A profile-level ``locations:`` list still wins for the default region,
        so an existing profile keeps behaving exactly as it did.
        """
        region = self.region(key)
        if self.locations and (key or self.active_region) == self.active_region:
            return self.locations
        return region.locations

    # ------------------------------------------------------------- accessors
    def all_titles(self) -> list[str]:
        """Every search query across all clusters, deduped, order preserved."""
        seen, out = set(), []
        for cluster in self.titles.values():
            for t in cluster:
                k = t.lower().strip()
                if k and k not in seen:
                    seen.add(k)
                    out.append(t.strip())
        return out

    def cluster_for(self, title_query: str) -> str:
        low = title_query.lower().strip()
        for cluster, items in self.titles.items():
            if any(low == t.lower().strip() for t in items):
                return cluster
        return "other"

    def all_skills(self) -> list[str]:
        out: list[str] = []
        for items in self.skills.values():
            out.extend(items)
        return out

    def skill_weight(self, skill: str) -> float:
        """Expert skills count for more than ones you merely list."""
        weights = {"expert": 1.0, "proficient": 0.7, "familiar": 0.4}
        low = skill.lower()
        for tier, items in self.skills.items():
            if any(low == s.lower() for s in items):
                return weights.get(tier, 0.6)
        return 0.5

    def resume_text(self) -> str:
        if self._resume_text:
            return self._resume_text
        if self.resume_path:
            path = Path(self.resume_path)
            if not path.is_absolute():
                path = APP_ROOT / self.resume_path
            if path.exists():
                self._resume_text = load_resume_text(path)
        return self._resume_text


# --------------------------------------------------------------------- resume
def load_resume_text(path: str | Path) -> str:
    """Extract plain text from a PDF, DOCX, MD or TXT resume."""
    path = Path(path)
    suffix = path.suffix.lower()

    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("pip install pypdf to read PDF resumes") from exc
        reader = PdfReader(str(path))
        chunks = []
        for page in reader.pages:
            try:
                chunks.append(page.extract_text() or "")
            except Exception:
                continue
        return _clean("\n".join(chunks))

    if suffix == ".docx":
        try:
            import docx  # type: ignore
        except ImportError as exc:
            raise RuntimeError("pip install python-docx to read .docx resumes") from exc
        d = docx.Document(str(path))
        return _clean("\n".join(p.text for p in d.paragraphs))

    return _clean(path.read_text(encoding="utf-8", errors="replace"))


BULLET = "•"

# Bullet glyphs seen in PDFs and Word exports. U+F0B7 and its neighbours are
# the Symbol-font private-use codepoints Word writes its bullets into, which
# is what this CV uses - without them nothing would register as a bullet.
_BULLET_CHARS = "•▪●∙·"


def _clean(text: str) -> str:
    """Normalise whitespace and put every bullet glyph at a line start.

    Marking bullets explicitly is what lets ``resume_bullets`` tell a new
    bullet apart from the wrapped continuation of the previous one.
    """
    text = re.sub("[" + _BULLET_CHARS + r"]\s*", "\n" + BULLET + " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# Lines that terminate a bullet rather than continuing it: a bare year, or a
# dated role heading such as "Sep 2021 - Present".
_DATE_LINE = re.compile(
    r"^\s*(?:(?:19|20)\d{2}\s*[-–—]"
    r"|\w{3,9}\s+(?:19|20)\d{2}\s*[-–—]"
    r"|(?:19|20)\d{2}\s*$)",
    re.I,
)


def resume_bullets(text: str, min_len: int = 45) -> list[str]:
    """Split resume text into achievement bullets, reflowing wrapped lines.

    A PDF breaks one bullet across several lines, so treating each line as its
    own bullet truncates them mid-sentence. Here a new bullet begins only at a
    bullet marker, and following lines fold back into it until the next
    marker, a blank line, a section header, or a new dated role.
    """
    marked: list[str] = []
    unmarked: list[str] = []
    current: list[str] = []
    current_is_marked = False

    def flush() -> None:
        nonlocal current_is_marked
        if not current:
            return
        joined = re.sub(r"\s+", " ", " ".join(current)).strip(" \t-*")
        if len(joined) >= min_len and not re.match(r"^(https?://|doi\.org)", joined, re.I):
            (marked if current_is_marked else unmarked).append(joined)
        current.clear()
        current_is_marked = False

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            flush()
            continue
        if line.startswith(BULLET):
            flush()
            current.append(line[len(BULLET):].strip())
            current_is_marked = True
            continue
        if line.isupper() or _DATE_LINE.match(line):
            flush()
            continue
        if current:
            current.append(line)
        elif len(line) >= min_len:
            # Prose paragraph carrying no bullet marker.
            current.append(line)
    flush()

    # Bulleted lines are the achievements; unmarked prose is usually the
    # header block, education, or publication list. Only fall back to prose
    # for resumes that use no bullet glyphs at all.
    return marked or unmarked


def extract_keywords(text: str, top: int = 60) -> list[str]:
    """Frequency-ranked content words, used when bootstrapping a new profile."""
    stop = _STOPWORDS
    words = re.findall(r"[a-zA-Z][a-zA-Z\-\.]{2,}", text.lower())
    freq: dict[str, int] = {}
    for w in words:
        w = w.strip(".-")
        if len(w) < 3 or w in stop:
            continue
        freq[w] = freq.get(w, 0) + 1
    return [w for w, _ in sorted(freq.items(), key=lambda kv: -kv[1])[:top]]


_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "was", "were", "are", "have",
    "has", "had", "not", "but", "you", "your", "our", "their", "its", "his", "her",
    "they", "them", "will", "would", "can", "could", "should", "may", "might", "must",
    "into", "over", "under", "about", "across", "through", "during", "before", "after",
    "using", "used", "use", "including", "include", "included", "also", "such", "than",
    "then", "when", "where", "which", "who", "whom", "whose", "what", "how", "why",
    "all", "any", "each", "more", "most", "other", "some", "only", "own", "same", "too",
    "very", "just", "one", "two", "three", "new", "per", "via", "within", "between",
    "phd", "university", "college", "department", "email", "phone", "address", "cv",
}
