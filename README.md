# Vinyl Finder (PWA + FastAPI)

- `frontend/`: installable PWA with camera capture and candidate selection
- `backend/`: FastAPI service calling Google Cloud Vision Web Detection

## Local quickstart

### Backend
```bash
cd backend
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
export GOOGLE_APPLICATION_CREDENTIALS="/absolute/path/to/service-account.json"
uvicorn main:app --reload --port 8000
```

### Frontend
```bash
cd ../frontend
python -m http.server 5173
```

Open: http://localhost:5173

## Next iteration
- Implement `/api/resolve` to enrich with year/genres via a music metadata provider.

## Discogs metadata enrichment

The backend `/api/resolve` uses the Discogs Database Search API to populate `genres`, `year`, and `decade`.
Set `DISCOGS_TOKEN` in your Cloud Run environment variables.
