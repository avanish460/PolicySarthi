# Policy Sarthi - Run Guide

## Recent Updates (April 2026)

- Added Sarvam configuration loading from `backend/.env` and health visibility via `sarvamConfigured`.
- Fixed Sarvam TTS integration to parse SDK `audios[]` payload correctly.
- Added proxy-safe Sarvam client setup (`trust_env=False`) so local proxy env vars do not block STT/TTS.
- Added voice-input auto-submit in Streamlit (no Send click required after recording).
- Enforced response language for voice queries to follow STT detected language.
- Enforced Sarvam-only voice playback path for voice queries.
- Updated playback UX to a compact circular play/pause control.
- Added one-time autoplay persistence so long voice responses are not cut by reruns.
- Feedback loop is stored in `feedback` table with query/doc metadata and optional comment/correction.
- Ingested additional official NHA AB PM-JAY corpus docs:
  - `DOC-1EF791AA` AB PM-JAY Operation Manual (April 2022)
  - `DOC-702346A9` AB PM-JAY Health Benefit Package 2.2 Manual
  - `DOC-AF37B50B` AB PM-JAY Consolidated Standard Treatment Guidelines (Sets 1-28)
  - `DOC-41FC844E` AB PM-JAY Hospital Empanelment Module SOP (Self Help Guide)
  - `DOC-EACD9E41` AB PM-JAY STG Manual Booklet (Process Customization)
  - `DOC-8D999BD1` AB PM-JAY Policy Brief: Impact on Inpatient Out-of-Pocket Expenditure
- Ingested additional dummy hospital operation corpus docs:
  - `DOC-6B88D267` Hospital Emergency Triage and Stabilization Policy
  - `DOC-05C5A541` Medication Safety and High-Alert Drug Policy
  - `DOC-A0A3D18F` Operation Theatre Sterility and Time-Out SOP
  - `DOC-D9309182` ICU Admission, Transfer, and Escalation SOP
  - `DOC-16F4DDCF` Infection Prevention and Biomedical Waste Segregation SOP
  - `DOC-5EC259FA` Patient Discharge Planning and Follow-Up Policy
  - `DOC-F58019EE` Hospital Billing Transparency and Estimate Disclosure Policy
  - `DOC-88AE1DF1` Ayushman Claim Documentation and Pre-Authorization Workflow

This project runs as:
- Backend: Flask API (`backend/app.py`) on `http://localhost:5000`
- Frontend: Streamlit chat UI (`frontend/main.py`) on `http://localhost:8501`

The frontend sends user queries to backend APIs:
- `POST /api/auth/login`
- `POST /api/query`
- `GET /api/health`

## Prerequisites

- Windows PowerShell
- Python 3.12+ (recommended)
- Virtual environment available at `.venv`

## 1. Open Project Folder

```powershell
cd "C:\Users\uadj23\Documents\PolicySarthi"
```

## 2. Activate Virtual Environment

```powershell
.\.venv\Scripts\Activate.ps1
```

## 3. Install Dependencies

Install root dependencies (includes Streamlit):

```powershell
python -m pip install -r requirements.txt
```

Install backend dependencies:

```powershell
python -m pip install -r backend\requirements.txt
```

## 4. Start Backend

Run in Terminal 1:

```powershell
python backend\app.py
```

Backend base URL:
- `http://localhost:5000`

Health check:
- `http://localhost:5000/api/health`

## 5. Start Frontend

Run in Terminal 2:

```powershell
streamlit run frontend\main.py
```

Frontend URL:
- `http://localhost:8501`

## 6. Login In UI

Use sidebar credentials (seed users):
- `admin / admin123`
- `staff / staff123`

Keep backend URL in sidebar as:
- `http://localhost:5000`

## 7. Test Query

Try this query in chat:

```text
Patient ke Ayushman claim ke liye kaunse documents chahiye?
```

The UI behavior:
- shows `Thinking.......` while waiting
- displays only final response text
- hides any `<think>` metadata if returned by model/backend

## Voice Behavior (Current)

- Voice input uses Sarvam STT via `POST /api/voice/transcribe`.
- After transcription, query is auto-submitted from frontend.
- Query language follows detected STT language code (`hi-IN`, `ta-IN`, `mr-IN`, etc.).
- Voice response is generated from Sarvam TTS (`include_voice=true` path).
- Voice speaks only final cleaned summary text (no hidden reasoning tags).
- Playback uses a compact circular play/pause control.

## 8. Run Backend Tests

```powershell
.\.venv\Scripts\python -m unittest discover -s tests -p "test_*.py" -v
```

## API Connection Flow (Frontend -> Backend)

1. Frontend calls `POST /api/auth/login` to get bearer token.
2. For voice input, frontend calls `POST /api/voice/transcribe` and receives `{ transcript, languageCode }`.
3. Frontend calls `POST /api/query` with `{ query, language, include_voice }`.
4. Backend returns response JSON including `summary` and optional `voicePlayback`.
5. Frontend displays streamed `summary` and voice controls when audio is available.

## Feedback Loop Data

Thumb actions submit this payload to `POST /api/feedback`:

```json
{
  "query": "string",
  "rating": 1,
  "queryLogId": 123,
  "topDocument": "string",
  "topDocumentId": "DOC-001",
  "comment": "optional",
  "correction": "optional"
}
```

Stored in SQLite table: `feedback` (`backend/data/app.db`).

## Troubleshooting

- `Backend unreachable` in UI:
  - confirm backend terminal is running
  - confirm URL is `http://localhost:5000`

- `Upload failed: [WinError 10054] An existing connection was forcibly closed by the remote host`:
  - this can happen if Flask auto-reloader restarts backend while uploaded file is being saved
  - backend now starts with `use_reloader=False` by default to prevent upload interruption
  - restart backend and retry upload

- `Login failed`:
  - verify username/password from seed list above

- `No summary text found in backend response`:
  - backend responded without expected `summary` field
  - check backend logs for errors

## Important Files

- `backend/app.py` - Flask API entry
- `backend/services/assistant.py` - assistant query logic
- `backend/data/documents.json` - seeded corpus
- `frontend/main.py` - Streamlit UI
