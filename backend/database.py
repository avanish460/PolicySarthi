import json
import sqlite3
import csv
from datetime import datetime
from pathlib import Path

from werkzeug.security import generate_password_hash

SEED_USERS = [
    {
        "id": "USR-001",
        "username": "admin",
        "password": "admin123",
        "display_name": "Aditi Sharma",
        "role": "admin",
        "department": "Administration",
    },
    {
        "id": "USR-002",
        "username": "staff",
        "password": "staff123",
        "display_name": "Rahul Verma",
        "role": "staff",
        "department": "Insurance Desk",
    },
    {
        "id": "USR-003",
        "username": "auditor",
        "password": "audit123",
        "display_name": "Neha Iyer",
        "role": "auditor",
        "department": "Compliance",
    },
    {
        "id": "USR-004",
        "username": "user",
        "password": "user123",
        "display_name": "General User",
        "role": "user",
        "department": "General",
    },
]

DUMMY_AYUSHMAN_DOC_IDS = {
    "DOC-001",
    "DOC-002",
    "DOC-003",
    "DOC-005",
    "DOC-006",
    "DOC-007",
    "DOC-009",
}


def initialize_database(base_dir: Path):
    data_dir = base_dir / "data"
    storage_dir = base_dir / "storage" / "uploads"
    storage_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "app.db"

    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    cursor = connection.cursor()

    cursor.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            display_name TEXT NOT NULL,
            role TEXT NOT NULL,
            department TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS documents (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            document_type TEXT NOT NULL,
            category TEXT NOT NULL,
            department TEXT NOT NULL,
            insurance_scheme TEXT NOT NULL,
            effective_date TEXT,
            language TEXT NOT NULL,
            version TEXT NOT NULL,
            summary TEXT NOT NULL,
            content TEXT NOT NULL,
            file_name TEXT NOT NULL,
            file_path TEXT NOT NULL,
            last_updated TEXT NOT NULL,
            uploaded_by TEXT NOT NULL,
            access_roles TEXT NOT NULL DEFAULT 'admin,staff,auditor,user'
        );

        CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            content TEXT NOT NULL,
            FOREIGN KEY(document_id) REFERENCES documents(id)
        );

        CREATE TABLE IF NOT EXISTS structured_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id TEXT NOT NULL,
            row_index INTEGER NOT NULL,
            content_json TEXT NOT NULL,
            search_text TEXT NOT NULL,
            FOREIGN KEY(document_id) REFERENCES documents(id)
        );

        CREATE INDEX IF NOT EXISTS idx_structured_records_document_id
        ON structured_records(document_id);

        CREATE TABLE IF NOT EXISTS query_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            query TEXT NOT NULL,
            detected_language TEXT NOT NULL,
            top_document TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query_log_id INTEGER,
            user_id TEXT NOT NULL,
            query TEXT NOT NULL,
            top_document_id TEXT,
            top_document TEXT,
            rating INTEGER NOT NULL,
            comment TEXT,
            correction TEXT,
            created_at TEXT NOT NULL
        );
        """
    )

    secure_seed_users = [
        {
            **user,
            "password": generate_password_hash(user["password"]),
        }
        for user in SEED_USERS
    ]

    cursor.executemany(
        """
        INSERT OR REPLACE INTO users (id, username, password, display_name, role, department)
        VALUES (:id, :username, :password, :display_name, :role, :department)
        """,
        secure_seed_users,
    )

    document_columns = {
        row["name"]
        for row in cursor.execute("PRAGMA table_info(documents)").fetchall()
    }
    if "access_roles" not in document_columns:
        cursor.execute(
            "ALTER TABLE documents ADD COLUMN access_roles TEXT NOT NULL DEFAULT 'admin,staff,auditor,user'"
        )

    seed_documents = json.loads((data_dir / "documents.json").read_text(encoding="utf-8"))

    # Remove known dummy Ayushman records so only official-source Ayushman data is retained.
    for doc_id in DUMMY_AYUSHMAN_DOC_IDS:
        cursor.execute("DELETE FROM chunks WHERE document_id = ?", (doc_id,))
        cursor.execute("DELETE FROM structured_records WHERE document_id = ?", (doc_id,))
        cursor.execute("DELETE FROM documents WHERE id = ?", (doc_id,))

    for document in seed_documents:
        if document["id"] in DUMMY_AYUSHMAN_DOC_IDS:
            continue
        cursor.execute(
            """
            INSERT OR REPLACE INTO documents (
                id, title, document_type, category, department, insurance_scheme,
                effective_date, language, version, summary, content, file_name,
                file_path, last_updated, uploaded_by, access_roles
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                document["id"],
                document["title"],
                document.get("document_type", document["category"]),
                document["category"],
                document["department"],
                document.get("insurance_scheme", "General"),
                document.get("effective_date", document["last_updated"]),
                document.get("language", "en-IN"),
                document.get("version", "v1"),
                document["summary"],
                document["content"],
                f"{document['id']}.txt",
                str(storage_dir / f"{document['id']}.txt"),
                document["last_updated"],
                "USR-001",
                document.get("access_roles", "admin,staff,auditor,user"),
            ),
        )
        cursor.execute("DELETE FROM chunks WHERE document_id = ?", (document["id"],))
        for index, chunk in enumerate(chunk_text(document["content"])):
            cursor.execute(
                "INSERT INTO chunks (document_id, chunk_index, content) VALUES (?, ?, ?)",
                (document["id"], index, chunk),
            )

    official_ayushman_path = data_dir / "official_ayushman_documents.json"
    if official_ayushman_path.exists():
        official_ayushman_documents = json.loads(official_ayushman_path.read_text(encoding="utf-8"))
        for document in official_ayushman_documents:
            cursor.execute(
                """
                INSERT OR REPLACE INTO documents (
                    id, title, document_type, category, department, insurance_scheme,
                    effective_date, language, version, summary, content, file_name,
                    file_path, last_updated, uploaded_by, access_roles
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    document["id"],
                    document["title"],
                    document.get("document_type", document["category"]),
                    document["category"],
                    document["department"],
                    document.get("insurance_scheme", "Ayushman Bharat"),
                    document.get("effective_date", document["last_updated"]),
                    document.get("language", "en-IN"),
                    document.get("version", "v1"),
                    document["summary"],
                    document["content"],
                    f"{document['id']}.txt",
                    str(storage_dir / f"{document['id']}.txt"),
                    document["last_updated"],
                    "USR-001",
                    document.get("access_roles", "admin,staff,auditor,user"),
                ),
            )
            cursor.execute("DELETE FROM chunks WHERE document_id = ?", (document["id"],))
            for index, chunk in enumerate(chunk_text(document["content"])):
                cursor.execute(
                    "INSERT INTO chunks (document_id, chunk_index, content) VALUES (?, ?, ?)",
                    (document["id"], index, chunk),
                )

    structured_manifest_path = data_dir / "structured_documents.json"
    if structured_manifest_path.exists():
        structured_documents = json.loads(structured_manifest_path.read_text(encoding="utf-8"))
        for document in structured_documents:
            data_file_path = data_dir / document["data_file"]
            if not data_file_path.exists():
                continue

            content = _structured_file_preview_text(data_file_path)
            last_updated = document.get("last_updated", now_iso()[:10])
            cursor.execute(
                """
                INSERT OR REPLACE INTO documents (
                    id, title, document_type, category, department, insurance_scheme,
                    effective_date, language, version, summary, content, file_name,
                    file_path, last_updated, uploaded_by, access_roles
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    document["id"],
                    document["title"],
                    document.get("document_type", "Structured Dataset"),
                    document.get("category", "Structured Data"),
                    document.get("department", "Administration"),
                    document.get("insurance_scheme", "General"),
                    document.get("effective_date", last_updated),
                    document.get("language", "en-IN"),
                    document.get("version", "v1"),
                    document.get("summary", "Structured hospital dataset"),
                    content,
                    data_file_path.name,
                    str(data_file_path),
                    last_updated,
                    "USR-001",
                    document.get("access_roles", "admin,staff,auditor,user"),
                ),
            )
            cursor.execute("DELETE FROM chunks WHERE document_id = ?", (document["id"],))
            for index, chunk in enumerate(chunk_text(content)):
                cursor.execute(
                    "INSERT INTO chunks (document_id, chunk_index, content) VALUES (?, ?, ?)",
                    (document["id"], index, chunk),
                )

    connection.commit()

    users = [
        dict(row)
        for row in cursor.execute(
            "SELECT id, username, password, display_name, role, department FROM users"
        ).fetchall()
    ]
    connection.close()

    return {
        "db_path": db_path,
        "storage_dir": storage_dir,
        "users": users,
    }


def chunk_text(text: str, max_chars: int = 500):
    words = text.split()
    chunks = []
    current = []
    current_len = 0
    for word in words:
        projected = current_len + len(word) + 1
        if projected > max_chars and current:
            chunks.append(" ".join(current))
            current = [word]
            current_len = len(word)
        else:
            current.append(word)
            current_len = projected
    if current:
        chunks.append(" ".join(current))
    return chunks


def now_iso():
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _structured_file_preview_text(file_path: Path, max_rows: int = 120):
    suffix = file_path.suffix.lower()
    if suffix == ".csv":
        rows = []
        try:
            with file_path.open("r", encoding="utf-8", errors="ignore", newline="") as handle:
                reader = csv.DictReader(handle)
                for index, record in enumerate(reader):
                    if index >= max_rows:
                        break
                    rendered = ", ".join(
                        f"{key}={str(value).strip()}"
                        for key, value in (record or {}).items()
                        if str(value).strip()
                    )
                    if rendered:
                        rows.append(f"Row {index + 1}: {rendered}")
        except Exception:
            return file_path.read_text(encoding="utf-8", errors="ignore")[:15000]
        return "\n".join(rows)

    if suffix == ".json":
        try:
            payload = json.loads(file_path.read_text(encoding="utf-8", errors="ignore"))
            rendered_lines = []
            if isinstance(payload, list):
                source_rows = payload[:max_rows]
            elif isinstance(payload, dict):
                source_rows = [payload]
            else:
                source_rows = []
            for index, row in enumerate(source_rows):
                if not isinstance(row, dict):
                    rendered_lines.append(f"Row {index + 1}: {str(row)}")
                    continue
                rendered = ", ".join(
                    f"{key}={str(value).strip()}"
                    for key, value in row.items()
                    if str(value).strip()
                )
                if rendered:
                    rendered_lines.append(f"Row {index + 1}: {rendered}")
            return "\n".join(rendered_lines)
        except Exception:
            return file_path.read_text(encoding="utf-8", errors="ignore")[:15000]

    return file_path.read_text(encoding="utf-8", errors="ignore")[:15000]
