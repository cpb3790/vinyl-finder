# Vinyl Finder Backend (FastAPI)

## Local run

```bash
cd backend
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
export GOOGLE_APPLICATION_CREDENTIALS="/absolute/path/to/service-account.json"
uvicorn main:app --reload --port 8000
```

Health check:
- http://localhost:8000/health

Identify:
- POST http://localhost:8000/api/identify (multipart field name `file`)

## Discogs (for /api/resolve enrichment)

Set environment variables:

- `DISCOGS_TOKEN` (personal access token from Discogs settings)
- `DISCOGS_USER_AGENT` (optional but recommended; e.g. "VinylFinder/0.1 +https://yourdomain")

These are used server-side only.
