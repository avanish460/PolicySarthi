# Policy Sarthi - Run Guide

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
cd "c:\Users\vmadmin\Downloads\AI_Powered_Inteligent_Doc_Policy (2)\AI_Powered_Inteligent_Doc_Policy"
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
- `auditor / audit123`
- `user / user123`

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

## API Connection Flow (Frontend -> Backend)

1. Frontend calls `POST /api/auth/login` to get bearer token.
2. Frontend calls `POST /api/query` with `{ query, language, include_voice }`.
3. Backend returns response JSON; frontend displays `summary` only.

## Troubleshooting

- `Backend unreachable` in UI:
  - confirm backend terminal is running
  - confirm URL is `http://localhost:5000`

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
