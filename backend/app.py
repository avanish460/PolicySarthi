from pathlib import Path

from flask import Flask, jsonify, redirect, request
from flask_cors import CORS
from flasgger import Swagger

try:
    from .auth import require_auth, seed_auth_state
    from .config import settings
    from .database import initialize_database
    from .services.assistant import HospitalAssistantService
except ImportError:
    from auth import require_auth, seed_auth_state
    from config import settings
    from database import initialize_database
    from services.assistant import HospitalAssistantService


BASE_DIR = Path(__file__).resolve().parent

app = Flask(__name__)
CORS(app)
app.config["SWAGGER"] = {
    "title": "Hospital Policy & Ayushman Claim Assistant API",
    "uiversion": 3,
}
swagger = Swagger(
    app,
    template={
        "swagger": "2.0",
        "info": {
            "title": "Hospital Policy & Ayushman Claim Assistant API",
            "description": "API for multilingual hospital policy retrieval, Ayushman claim guidance, document ingestion, analytics, and voice-ready workflows.",
            "version": "1.0.0",
        },
        "basePath": "/",
        "schemes": ["http"],
    },
)

state = initialize_database(BASE_DIR)
seed_auth_state(state["users"])
assistant = HospitalAssistantService(state, settings)


@app.get("/")
def root():
    return redirect("/apidocs/")


@app.get("/api/health")
def health_check():
    """
    Health check endpoint.
    ---
    tags:
      - System
    responses:
      200:
        description: Service health and Sarvam integration status.
    """
    return jsonify(
        {
            "status": "ok",
            "service": "hospital-policy-assistant",
            "sarvamConfigured": assistant.sarvam.enabled,
        }
    )


@app.post("/api/auth/login")
def login():
    """
    Login and receive a bearer token.
    ---
    tags:
      - Authentication
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - username
            - password
          properties:
            username:
              type: string
              example: admin
            password:
              type: string
              example: admin123
    responses:
      200:
        description: Token and user profile.
      401:
        description: Invalid credentials.
    """
    payload = request.get_json(silent=True) or {}
    username = (payload.get("username") or "").strip()
    password = (payload.get("password") or "").strip()
    if not username or not password:
        return jsonify({"error": "Username and password are required"}), 400
    result = assistant.login(username, password)
    if not result:
        return jsonify({"error": "Invalid credentials"}), 401
    return jsonify(result)


@app.get("/api/auth/me")
@require_auth()
def me(current_user):
    """
    Return the current authenticated user.
    ---
    tags:
      - Authentication
    security:
      - Bearer: []
    responses:
      200:
        description: Current user profile.
    """
    return jsonify({"user": current_user})


@app.get("/api/dashboard")
@require_auth()
def dashboard(current_user):
    """
    Get role-aware dashboard data.
    ---
    tags:
      - Dashboard
    security:
      - Bearer: []
    responses:
      200:
        description: Dashboard stats, top queries, and highlights.
    """
    return jsonify(assistant.get_dashboard_data(current_user))


@app.get("/api/documents")
@require_auth()
def list_documents(current_user):
    """
    List indexed hospital documents.
    ---
    tags:
      - Documents
    security:
      - Bearer: []
    responses:
      200:
        description: Document list with metadata.
    """
    return jsonify({"documents": assistant.get_documents(current_user)})


@app.get("/api/documents/<document_id>")
@require_auth()
def get_document(current_user, document_id: str):
    """
    Get a single indexed document by id.
    ---
    tags:
      - Documents
    security:
      - Bearer: []
    parameters:
      - in: path
        name: document_id
        type: string
        required: true
    responses:
      200:
        description: Full document details and chunks.
      404:
        description: Document not found.
    """
    document = assistant.get_document(document_id, current_user)
    if not document:
        return jsonify({"error": "Document not found"}), 404
    return jsonify(document)


@app.get("/api/sample-queries")
@require_auth()
def sample_queries(current_user):
    """
    Get sample assistant queries.
    ---
    tags:
      - Assistant
    security:
      - Bearer: []
    responses:
      200:
        description: Example user queries.
    """
    return jsonify({"queries": assistant.sample_queries})


@app.get("/api/search")
@require_auth()
def search_documents(current_user):
    """
    Search indexed documents.
    ---
    tags:
      - Documents
    security:
      - Bearer: []
    parameters:
      - in: query
        name: q
        type: string
        required: false
        example: rejection
    responses:
      200:
        description: Ranked search results.
    """
    query = (request.args.get("q") or "").strip()
    results = assistant.search_documents(query, current_user)
    return jsonify({"query": query, "results": results})


@app.post("/api/query")
@require_auth()
def query_assistant(current_user):
    """
    Ask the assistant a policy or claim question.
    ---
    tags:
      - Assistant
    security:
      - Bearer: []
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - query
          properties:
            query:
              type: string
              example: Patient ke Ayushman claim ke liye kaunse documents chahiye?
            language:
              type: string
              example: auto
            include_voice:
              type: boolean
              example: true
    responses:
      200:
        description: Grounded answer with sources and claim checklist.
      400:
        description: Missing query.
    """
    payload = request.get_json(silent=True) or {}
    query = (payload.get("query") or "").strip()
    if not query:
        return jsonify({"error": "Query is required"}), 400

    language = payload.get("language") or "auto"
    include_voice = bool(payload.get("include_voice"))
    response = assistant.answer_query(
        user=current_user,
        query=query,
        preferred_language=language,
        include_voice=include_voice,
    )
    return jsonify(response)


@app.post("/api/feedback")
@require_auth()
def submit_feedback(current_user):
    """
    Submit human feedback for an assistant response.
    ---
    tags:
      - Assistant
    security:
      - Bearer: []
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - query
            - rating
          properties:
            query:
              type: string
            queryLogId:
              type: integer
            topDocument:
              type: string
            topDocumentId:
              type: string
            rating:
              type: integer
              example: 1
            comment:
              type: string
            correction:
              type: string
    responses:
      200:
        description: Feedback accepted and stored.
      400:
        description: Invalid feedback payload.
    """
    payload = request.get_json(silent=True) or {}
    result = assistant.submit_feedback(current_user, payload)
    if "error" in result:
        return jsonify(result), 400
    return jsonify(result)


@app.post("/api/documents/upload")
@require_auth(roles={"admin"})
def upload_document(current_user):
    """
    Upload and index a new hospital document.
    ---
    tags:
      - Documents
    consumes:
      - multipart/form-data
    security:
      - Bearer: []
    parameters:
      - in: formData
        name: file
        type: file
        required: true
      - in: formData
        name: title
        type: string
      - in: formData
        name: document_type
        type: string
      - in: formData
        name: department
        type: string
      - in: formData
        name: insurance_scheme
        type: string
      - in: formData
        name: effective_date
        type: string
      - in: formData
        name: language
        type: string
      - in: formData
        name: version
        type: string
      - in: formData
        name: summary
        type: string
    responses:
      201:
        description: Document uploaded and indexed.
    """
    if "file" not in request.files:
        return jsonify({"error": "Upload requires a file field"}), 400

    upload = request.files["file"]
    metadata = {
        "title": request.form.get("title") or upload.filename,
        "document_type": request.form.get("document_type") or "Policy",
        "department": request.form.get("department") or current_user.get("department") or "Administration",
        "insurance_scheme": request.form.get("insurance_scheme") or "General",
        "effective_date": request.form.get("effective_date") or "",
        "language": request.form.get("language") or "en-IN",
        "version": request.form.get("version") or "v1",
        "summary": request.form.get("summary") or "",
    }
    result = assistant.ingest_document(current_user, upload, metadata)
    return jsonify(result), 201


@app.post("/api/compare-documents")
@require_auth()
def compare_documents(current_user):
    """
    Compare two indexed documents.
    ---
    tags:
      - Documents
    security:
      - Bearer: []
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - leftDocumentId
            - rightDocumentId
          properties:
            leftDocumentId:
              type: string
              example: DOC-001
            rightDocumentId:
              type: string
              example: DOC-002
    responses:
      200:
        description: Comparison summary and diff preview.
      404:
        description: Document not found.
    """
    payload = request.get_json(silent=True) or {}
    left_id = payload.get("leftDocumentId")
    right_id = payload.get("rightDocumentId")
    if not left_id or not right_id:
        return jsonify({"error": "Both document ids are required"}), 400
    result = assistant.compare_documents(left_id, right_id)
    if "error" in result:
        return jsonify(result), 404
    return jsonify(result)


@app.get("/api/analytics")
@require_auth(roles={"admin", "auditor"})
def analytics(current_user):
    """
    Get query analytics and language breakdown.
    ---
    tags:
      - Analytics
    security:
      - Bearer: []
    responses:
      200:
        description: Aggregated analytics for admin and auditor roles.
    """
    return jsonify(assistant.get_analytics())


@app.post("/api/voice/transcribe")
@require_auth()
def transcribe(current_user):
    """
    Transcribe uploaded audio or use a text hint fallback.
    ---
    tags:
      - Voice
    consumes:
      - multipart/form-data
    security:
      - Bearer: []
    parameters:
      - in: formData
        name: file
        type: file
        required: false
      - in: formData
        name: text_hint
        type: string
        required: false
    responses:
      200:
        description: Transcript and language information.
    """
    audio = request.files.get("file")
    text_hint = request.form.get("text_hint") or ""
    result = assistant.transcribe_audio(audio, text_hint)
    return jsonify(result)


@app.post("/api/voice/speak")
@require_auth()
def speak(current_user):
    """
    Generate voice playback for text.
    ---
    tags:
      - Voice
    security:
      - Bearer: []
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - text
          properties:
            text:
              type: string
            language:
              type: string
              example: hi-IN
    responses:
      200:
        description: Base64 audio payload or fallback demo payload.
    """
    payload = request.get_json(silent=True) or {}
    text = (payload.get("text") or "").strip()
    language = payload.get("language") or "hi-IN"
    if not text:
        return jsonify({"error": "Text is required"}), 400
    return jsonify(assistant.speak_text(text, language))


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
