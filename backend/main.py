from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import requests

# Google Vision client
# Install: google-cloud-vision
# Auth: use Application Default Credentials (ADC)
#   Local: set GOOGLE_APPLICATION_CREDENTIALS to a service account JSON path.
#   Cloud Run: assign a service account to the service and grant Vision API permissions.
try:
    from google.cloud import vision
except Exception:  # pragma: no cover
    vision = None




DISCOGS_API_BASE = "https://api.discogs.com"

def _discogs_token() -> str:
    token = os.getenv("DISCOGS_TOKEN", "").strip()
    if not token:
        raise HTTPException(
            status_code=500,
            detail="Discogs token not configured. Set DISCOGS_TOKEN on the backend.",
        )
    return token

def _discogs_headers() -> dict:
    # Discogs recommends sending a descriptive User-Agent; some requests may be rejected otherwise.
    ua = os.getenv("DISCOGS_USER_AGENT", "VinylFinder/0.1 +https://example.invalid").strip()
    return {
        "Authorization": f"Discogs token={_discogs_token()}",
        "User-Agent": ua,
        "Accept": "application/vnd.discogs.v2+json",
    }

def _discogs_get(path: str, params: dict | None = None) -> dict:
    url = f"{DISCOGS_API_BASE}{path}"
    resp = requests.get(url, headers=_discogs_headers(), params=params, timeout=25)
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Discogs API error ({resp.status_code}): {resp.text}")
    return resp.json()

def _decade_from_year(year: int | None) -> str | None:
    if not year:
        return None
    decade = (int(year) // 10) * 10
    return f"{decade}s"

def _normalize_lower(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())

def _pick_best_discogs_result(results: list, artist: str, album: str) -> dict | None:
    a = _normalize_lower(artist)
    al = _normalize_lower(album)

    def score(r: dict) -> float:
        title = _normalize_lower(r.get("title", ""))
        s = 0.0
        if a and a in title:
            s += 2.0
        if al and al in title:
            s += 2.0
        rtype = (r.get("type") or "").lower()
        if rtype in ("master", "release"):
            s += 1.0
        if r.get("year"):
            s += 0.2
        return s

    if not results:
        return None
    ranked = sorted(results, key=score, reverse=True)
    return ranked[0]


app = FastAPI(title="Vinyl Finder API", version="0.1.0")

# CORS: tighten this in production by setting FRONTEND_ORIGIN env var
frontend_origin = os.getenv("FRONTEND_ORIGIN", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[frontend_origin] if frontend_origin != "*" else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

MAX_IMAGE_BYTES = int(os.getenv("MAX_IMAGE_BYTES", str(8 * 1024 * 1024)))  # 8MB default


class Candidate(BaseModel):
    artist: str
    album: str
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: Dict[str, Any] = Field(default_factory=dict)


class IdentifyResponse(BaseModel):
    candidates: List[Candidate]
    debug: Optional[Dict[str, Any]] = None


class ResolveRequest(BaseModel):
    artist: str
    album: str


class ResolveResponse(BaseModel):
    artist: str
    album: str
    year: Optional[int] = None
    decade: Optional[str] = None
    genres: List[str] = Field(default_factory=list)
    pricing: Optional[Dict[str, Any]] = None  # placeholder for future


def _require_vision() -> None:
    if vision is None:
        raise HTTPException(
            status_code=500,
            detail=(
                "google-cloud-vision is not installed or failed to import. "
                "Install dependencies and restart the backend."
            ),
        )


def _read_image_bytes(upload: UploadFile) -> bytes:
    content = upload.file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file.")
    if len(content) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail=f"File too large. Limit is {MAX_IMAGE_BYTES} bytes.")
    return content


def _normalize_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _split_artist_album(text: str) -> Optional[Tuple[str, str]]:
    """Try to split a label into (artist, album) using common separators.

    Examples:
      - "Pink Floyd - The Dark Side of the Moon"
      - "Nirvana: Nevermind"
      - "The Clash – London Calling"
      - "Kind of Blue by Miles Davis"
    """
    t = _normalize_spaces(text)

    # Remove common suffix noise
    t = re.sub(r"\b(album cover|vinyl|record|lp|cd)\b", "", t, flags=re.IGNORECASE)
    t = _normalize_spaces(t)

    # Common separators
    seps = [" - ", " – ", " — ", ": "]
    for sep in seps:
        if sep in t:
            left, right = t.split(sep, 1)
            left = _normalize_spaces(left)
            right = _normalize_spaces(right)
            if left and right and len(left) >= 2 and len(right) >= 2:
                return left, right

    # "Album by Artist"
    m = re.match(r"(.+?)\s+by\s+(.+)$", t, flags=re.IGNORECASE)
    if m:
        album = _normalize_spaces(m.group(1))
        artist = _normalize_spaces(m.group(2))
        if artist and album:
            return artist, album

    return None


def _candidate_from_guess(label: str, rank: int) -> Optional[Candidate]:
    split = _split_artist_album(label)
    if not split:
        return None
    artist, album = split
    base = max(0.35, 0.75 - 0.10 * rank)  # rank-based base confidence
    return Candidate(
        artist=artist,
        album=album,
        confidence=min(1.0, base),
        evidence={"source": "bestGuessLabel", "label": label, "rank": rank},
    )


def _candidate_from_entity(desc: str, score: float) -> Optional[Candidate]:
    split = _split_artist_album(desc)
    if not split:
        return None
    artist, album = split
    s = float(score) if score is not None else 0.0
    conf = min(0.65, max(0.25, s))  # clamp into a conservative band
    return Candidate(
        artist=artist,
        album=album,
        confidence=conf,
        evidence={"source": "webEntity", "description": desc, "entity_score": s},
    )


def _dedupe_candidates(cands: List[Candidate]) -> List[Candidate]:
    seen: Dict[Tuple[str, str], Candidate] = {}
    for c in cands:
        key = (c.artist.lower(), c.album.lower())
        if key not in seen:
            seen[key] = c
        else:
            prev = seen[key]
            if c.confidence > prev.confidence:
                c.evidence = {"merged": [prev.evidence, c.evidence]}
                seen[key] = c
            else:
                prev.evidence = {"merged": [prev.evidence, c.evidence]}
                seen[key] = prev
    return sorted(seen.values(), key=lambda x: x.confidence, reverse=True)


def _call_web_detection(image_bytes: bytes) -> Dict[str, Any]:
    _require_vision()
    client = vision.ImageAnnotatorClient()
    image = vision.Image(content=image_bytes)
    response = client.web_detection(image=image)
    if response.error.message:
        raise HTTPException(status_code=502, detail=f"Vision API error: {response.error.message}")

    web = response.web_detection

    best_guesses = []
    if web.best_guess_labels:
        for i, bg in enumerate(web.best_guess_labels):
            best_guesses.append({"label": bg.label, "language_code": bg.language_code, "rank": i})

    entities = []
    if web.web_entities:
        for ent in web.web_entities:
            entities.append({
                "description": ent.description,
                "score": float(ent.score) if ent.score is not None else None,
            })

    pages = []
    if web.pages_with_matching_images:
        for p in web.pages_with_matching_images[:10]:
            pages.append({"url": p.url})

    return {"best_guess_labels": best_guesses, "web_entities": entities, "matching_pages": pages}


@app.post("/api/identify", response_model=IdentifyResponse)
async def identify(file: UploadFile = File(...), debug: bool = False) -> IdentifyResponse:
    """Upload a vinyl cover photo and return top artist/album candidates.

    This endpoint is designed to be conservative: it returns multiple candidates and
    expects the user to confirm.
    """
    if file.content_type not in ("image/jpeg", "image/png", "image/webp"):
        raise HTTPException(status_code=400, detail="Unsupported file type. Use JPG/PNG/WebP.")

    image_bytes = _read_image_bytes(file)
    vision_payload = _call_web_detection(image_bytes)

    candidates: List[Candidate] = []

    for bg in vision_payload.get("best_guess_labels", []):
        c = _candidate_from_guess(bg["label"], bg["rank"])
        if c:
            candidates.append(c)

    for ent in vision_payload.get("web_entities", []):
        desc = (ent.get("description") or "").strip()
        if not desc:
            continue
        c = _candidate_from_entity(desc, ent.get("score") or 0.0)
        if c:
            candidates.append(c)

    candidates = _dedupe_candidates(candidates)[:5]

    resp = IdentifyResponse(candidates=candidates)
    if debug:
        resp.debug = vision_payload
    return resp


@app.post("/api/resolve", response_model=ResolveResponse)
async def resolve(req: ResolveRequest) -> ResolveResponse:
    """Enrich a confirmed artist/album with Discogs metadata (Database Search API)."""
    artist = _normalize_spaces(req.artist)
    album = _normalize_spaces(req.album)
    if not artist or not album:
        raise HTTPException(status_code=400, detail="Artist and album are required.")

    params = {
        "type": "release",
        "artist": artist,
        "release_title": album,
        "per_page": 10,
        "page": 1,
    }
    search = _discogs_get("/database/search", params=params)
    results = search.get("results") or []

    best = _pick_best_discogs_result(results, artist, album)
    if not best:
        params2 = {"q": f"{artist} {album}", "type": "release", "per_page": 10, "page": 1}
        search2 = _discogs_get("/database/search", params=params2)
        best = _pick_best_discogs_result(search2.get("results") or [], artist, album)

    if not best:
        return ResolveResponse(artist=artist, album=album, year=None, decade=None, genres=[], pricing=None)

    year_val = best.get("year")
    try:
        year = int(year_val) if year_val else None
    except Exception:
        year = None
    decade = _decade_from_year(year)

    genres = []
    if isinstance(best.get("genre"), list):
        genres.extend(best.get("genre"))
    if isinstance(best.get("style"), list):
        genres.extend(best.get("style"))

    seen = set()
    genres_clean = []
    for g in genres:
        if not g:
            continue
        key = str(g).strip().lower()
        if key in seen:
            continue
        seen.add(key)
        genres_clean.append(str(g).strip())

    title = best.get("title") or ""
    discogs_artist, discogs_album = artist, album
    if " - " in title:
        left, right = title.split(" - ", 1)
        discogs_artist = _normalize_spaces(left)
        discogs_album = _normalize_spaces(right)

    return ResolveResponse(
        artist=discogs_artist,
        album=discogs_album,
        year=year,
        decade=decade,
        genres=genres_clean,
        pricing=None,
    )

@app.get("/health")
async def health() -> Dict[str, str]:
    return {"status": "ok"}
