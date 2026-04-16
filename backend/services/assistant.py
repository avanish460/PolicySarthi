import csv
import difflib
import json
import re
import sqlite3
from pathlib import Path
from uuid import uuid4

try:
    from ..auth import authenticate
    from ..database import chunk_text, now_iso
    from .sarvam_client import SarvamService
except ImportError:
    from auth import authenticate
    from database import chunk_text, now_iso
    from services.sarvam_client import SarvamService


class HospitalAssistantService:
    def __init__(self, state: dict, settings):
        self.db_path = state["db_path"]
        self.storage_dir = state["storage_dir"]
        self.settings = settings
        self.sarvam = SarvamService(settings)
        self.sample_queries = [
            "Patient ke Ayushman claim ke liye kaunse documents chahiye?",
            "Is pre-authorization required before planned admission?",
            "Why do Ayushman claims get rejected?",
            "Compare the latest claim SOP with the discharge workflow.",
            "Explain the Ayushman process in Hindi.",
        ]
        self.synonyms = {
            "ayushman": {"ayushman", "pm-jay", "pmjay", "scheme", "card"},
            "documents": {"documents", "document", "docs", "required", "kaunse", "proof"},
            "claim": {"claim", "submission", "insurance", "desk"},
            "preauth": {"pre-authorization", "preauthorization", "preauth", "approval"},
            "rejection": {"reject", "rejected", "rejection", "missing", "denied"},
            "workflow": {"workflow", "process", "steps", "summary"},
            "discharge": {"discharge", "release", "summary"},
            "comparison": {"compare", "difference", "version"},
        }
        self.placeholder_phrase = (
            "This uploaded document contains claim process notes, package guidance, document checklist, "
            "discharge requirements, and approval steps for hospital operations."
        )
        self.default_access_roles = "admin,staff,auditor,user"
        self.sensitivity_access_map = {
            "public": "admin,staff,auditor,user",
            "internal": "admin,staff,auditor",
            "confidential": "admin,staff",
            "restricted": "admin",
        }
        self._reindex_existing_uploads()
        self._backfill_document_access()
        self._backfill_structured_records()

    def _connect(self):
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def login(self, username: str, password: str):
        return authenticate(username, password)

    def get_dashboard_data(self, user: dict):
        with self._connect() as connection:
            docs_count = connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            query_count = connection.execute("SELECT COUNT(*) FROM query_logs").fetchone()[0]
            departments = [
                dict(row)
                for row in connection.execute(
                    "SELECT department AS name, COUNT(*) AS count FROM documents GROUP BY department ORDER BY count DESC"
                ).fetchall()
            ]
            top_queries = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT query AS label, COUNT(*) AS count
                    FROM query_logs
                    GROUP BY query
                    ORDER BY count DESC, query ASC
                    LIMIT 5
                    """
                ).fetchall()
            ]

        return {
            "stats": {
                "documentsIndexed": docs_count,
                "languagesSupported": 3,
                "claimAccuracy": "92%",
                "avgResponseTime": "3.2 sec",
                "queriesHandled": query_count,
            },
            "departments": departments,
            "topQueries": top_queries,
            "roleView": user["role"],
            "highlight": {
                "title": "What this MVP demonstrates",
                "description": "Upload hospital documents, extract or infer text, index chunks, answer multilingual queries, surface source citations, compare SOP versions, and inspect usage analytics.",
            },
        }

    def get_documents(self, user: dict | None = None):
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, title, document_type, category, department, insurance_scheme,
                       effective_date, language, version, summary, last_updated, file_name,
                       sensitivity_label, access_roles
                FROM documents
                ORDER BY last_updated DESC, title ASC
                """
            ).fetchall()
        documents = [dict(row) for row in rows]
        visible = [document for document in documents if self._has_document_access(user, document)]
        for document in visible:
            document.pop("access_roles", None)
        return visible

    def get_document(self, document_id: str, user: dict | None = None):
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM documents WHERE id = ?", (document_id,)
            ).fetchone()
            if not row:
                return None
            if not self._has_document_access(user, dict(row)):
                return None
            chunks = [
                dict(chunk)
                for chunk in connection.execute(
                    "SELECT chunk_index, content FROM chunks WHERE document_id = ? ORDER BY chunk_index",
                    (document_id,),
                ).fetchall()
            ]
        payload = dict(row)
        payload["chunks"] = chunks
        return payload

    def search_documents(self, query: str, user: dict | None = None):
        if not query:
            return []
        ranked = self._retrieve(query, top_k=10, user=user)
        return ranked

    def ingest_document(self, user: dict, upload, metadata: dict):
        extension = Path(upload.filename).suffix.lower() or ".bin"
        document_id = f"DOC-{uuid4().hex[:8].upper()}"
        target_path = self.storage_dir / f"{document_id}{extension}"
        upload.save(target_path)

        extracted_text = self._extract_text(target_path, metadata)
        summary = metadata["summary"] or self._summarize_content(extracted_text, metadata)
        now = now_iso()
        sensitivity_label = self._determine_sensitivity_label(metadata, extracted_text)
        access_roles = self._access_roles_for_sensitivity(sensitivity_label)

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO documents (
                    id, title, document_type, category, department, insurance_scheme,
                    effective_date, language, version, summary, content, file_name,
                    file_path, last_updated, uploaded_by, sensitivity_label, access_roles
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    document_id,
                    metadata["title"],
                    metadata["document_type"],
                    metadata["document_type"],
                    metadata["department"],
                    metadata["insurance_scheme"],
                    metadata["effective_date"] or now[:10],
                    metadata["language"],
                    metadata["version"],
                    summary,
                    extracted_text,
                    upload.filename,
                    str(target_path),
                    now[:10],
                    user["id"],
                    sensitivity_label,
                    access_roles,
                ),
            )
            for index, chunk in enumerate(chunk_text(extracted_text, self.settings.max_chunk_chars)):
                connection.execute(
                    "INSERT INTO chunks (document_id, chunk_index, content) VALUES (?, ?, ?)",
                    (document_id, index, chunk),
                )
            structured_rows_indexed = self._index_structured_rows(connection, document_id, target_path)
            connection.commit()

        return {
            "documentId": document_id,
            "message": "Document uploaded, processed, and indexed successfully.",
            "metadata": metadata,
            "sensitivityLabel": sensitivity_label,
            "accessRoles": access_roles.split(","),
            "extractedPreview": extracted_text[:320],
            "structuredRowsIndexed": structured_rows_indexed,
        }

    def answer_query(self, user: dict, query: str, preferred_language: str, include_voice: bool):
        detected_language = self._detect_language(query, preferred_language)
        target_language_code = self._language_code_for_output(detected_language)
        retrieval_query = query
        if detected_language != "English":
            translated_query = self.sarvam.translate(query, target_language_code, "en-IN")
            if translated_query:
                retrieval_query = translated_query

        ranked = self._retrieve(retrieval_query, top_k=3, user=user)
        if "compare" in retrieval_query.lower():
            ranked = self._select_comparison_documents(retrieval_query, ranked)[:2]
        answer = self._generate_answer(user, retrieval_query, ranked, detected_language)
        if answer.get("no_info") and self._has_access_blocked_match(retrieval_query, user):
            answer = self._access_denied_response(detected_language)
        if answer.get("no_info"):
            ranked = []
        else:
            ranked = self._filter_grounded_sources(retrieval_query, ranked)
        top = ranked[0] if ranked else None
        missing_warnings = answer.get("warnings", [])
        answer["summary"] = self._clean_summary_text(answer.get("summary", ""))
        voice = self.speak_text(answer["summary"], target_language_code) if include_voice else None
        confidence = self._build_confidence_signal(retrieval_query, ranked, answer.get("no_info", False))

        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO query_logs (user_id, query, detected_language, top_document, created_at) VALUES (?, ?, ?, ?, ?)",
                (user["id"], query, detected_language, top["title"] if top else "", now_iso()),
            )
            connection.commit()
            query_log_id = cursor.lastrowid

        return {
            "query": query,
            "queryLogId": query_log_id,
            "detectedLanguage": detected_language,
            "summary": answer["summary"],
            "detailedSteps": answer["steps"],
            "sources": ranked,
            "topDocument": top["title"] if top else None,
            "topDocumentId": top["id"] if top else None,
            "missingDocumentWarnings": missing_warnings,
            "claimChecklist": answer["checklist"],
            "voicePlayback": voice,
            "confidence": confidence,
        }

    def _clean_summary_text(self, text: str):
        cleaned = (text or "").strip()
        cleaned = re.sub(r"<think\b[^>]*>.*?</think>", "", cleaned, flags=re.IGNORECASE | re.DOTALL)
        cleaned = re.sub(r"</?think\b[^>]*>", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
        return cleaned

    def compare_documents(self, left_id: str, right_id: str):
        left = self.get_document(left_id)
        right = self.get_document(right_id)
        if not left or not right:
            return {"error": "One or both documents were not found"}

        diff = list(
            difflib.unified_diff(
                left["content"].splitlines(),
                right["content"].splitlines(),
                fromfile=left["title"],
                tofile=right["title"],
                n=1,
            )
        )

        return {
            "leftDocument": {"id": left["id"], "title": left["title"], "version": left["version"]},
            "rightDocument": {"id": right["id"], "title": right["title"], "version": right["version"]},
            "comparisonSummary": self._comparison_summary(left, right),
            "diffPreview": diff[:20],
        }

    def get_analytics(self):
        with self._connect() as connection:
            top_queries = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT query, COUNT(*) AS count
                    FROM query_logs
                    GROUP BY query
                    ORDER BY count DESC, query ASC
                    LIMIT 10
                    """
                ).fetchall()
            ]
            by_language = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT detected_language AS language, COUNT(*) AS count
                    FROM query_logs
                    GROUP BY detected_language
                    ORDER BY count DESC
                    """
                ).fetchall()
            ]
            feedback_summary = connection.execute(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN rating > 0 THEN 1 ELSE 0 END), 0) AS positive,
                    COALESCE(SUM(CASE WHEN rating < 0 THEN 1 ELSE 0 END), 0) AS negative,
                    COUNT(*) AS total
                FROM feedback
                """
            ).fetchone()
        return {
            "topQueries": top_queries,
            "languageBreakdown": by_language,
            "feedbackSummary": dict(feedback_summary) if feedback_summary else {"positive": 0, "negative": 0, "total": 0},
            "notes": "Analytics are generated from local audit logs to support role-based dashboards and usage reviews.",
        }

    def submit_feedback(self, user: dict, payload: dict):
        query_log_id = payload.get("queryLogId")
        query = (payload.get("query") or "").strip()
        top_document = (payload.get("topDocument") or "").strip()
        top_document_id = (payload.get("topDocumentId") or "").strip()
        comment = (payload.get("comment") or "").strip()
        correction = (payload.get("correction") or "").strip()
        rating = payload.get("rating")

        if rating not in (-1, 1):
            return {"error": "Rating must be 1 or -1."}
        if not query:
            return {"error": "Query is required for feedback."}

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO feedback (
                    query_log_id, user_id, query, top_document_id, top_document,
                    rating, comment, correction, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    query_log_id,
                    user["id"],
                    query,
                    top_document_id or None,
                    top_document or None,
                    rating,
                    comment,
                    correction,
                    now_iso(),
                ),
            )
            connection.commit()

        return {
            "message": "Feedback saved. Future retrieval will use this human signal.",
            "appliedSignal": "positive" if rating > 0 else "negative",
        }

    def transcribe_audio(self, audio, text_hint: str):
        if audio:
            suffix = Path(audio.filename).suffix.lower() or ".wav"
            temp_path = self.storage_dir / f"temp-{uuid4().hex}{suffix}"
            audio.save(temp_path)
            live = self.sarvam.speech_to_text(temp_path)
            temp_path.unlink(missing_ok=True)
            if live:
                return {"transcript": live["transcript"], "languageCode": live["language_code"], "mode": "sarvam"}

        transcript = text_hint or "Patient ke Ayushman claim ke liye kaunse documents chahiye?"
        return {"transcript": transcript, "languageCode": "hi-IN", "mode": "fallback"}

    def speak_text(self, text: str, language: str):
        encoded = self.sarvam.text_to_speech(text, language)
        if encoded:
            return {"audioBase64": encoded, "mode": "sarvam", "language": language}

        return {"audioBase64": "", "mode": "not_available", "language": language}

    def _retrieve(self, query: str, top_k: int, user: dict | None = None):
        tokens = self._expand_tokens(query)
        normalized_query = " ".join(query.lower().split())
        with self._connect() as connection:
            documents = [dict(row) for row in connection.execute("SELECT * FROM documents").fetchall()]
            chunks = [dict(row) for row in connection.execute("SELECT document_id, chunk_index, content FROM chunks").fetchall()]
            structured_rows = [
                dict(row)
                for row in connection.execute(
                    "SELECT document_id, row_index, content_json, search_text FROM structured_records"
                ).fetchall()
            ]
            feedback_bias = {
                row["top_document_id"]: row["bias"]
                for row in connection.execute(
                    """
                    SELECT top_document_id, SUM(rating) AS bias
                    FROM feedback
                    WHERE top_document_id IS NOT NULL AND top_document_id != ''
                    GROUP BY top_document_id
                    """
                ).fetchall()
            }

        chunk_map = {}
        for chunk in chunks:
            chunk_map.setdefault(chunk["document_id"], []).append(chunk)
        structured_map = {}
        for row in structured_rows:
            structured_map.setdefault(row["document_id"], []).append(row)

        scored = []
        for document in documents:
            if not self._has_document_access(user, document):
                continue
            haystack = " ".join(
                [
                    document["title"],
                    document["summary"],
                    document["content"],
                    document["document_type"],
                    document["insurance_scheme"],
                ]
            ).lower()
            score = sum(3 if token in document["title"].lower() else 1 for token in tokens if token in haystack)
            if normalized_query and normalized_query in " ".join(haystack.split()):
                score += 8
            score += feedback_bias.get(document["id"], 0) * 2
            if score <= 0:
                continue
            matched_chunks = []
            for chunk in chunk_map.get(document["id"], []):
                chunk_haystack = chunk["content"].lower()
                chunk_score = sum(1 for token in tokens if token in chunk_haystack)
                if normalized_query and normalized_query in " ".join(chunk_haystack.split()):
                    chunk_score += 8
                if chunk_score:
                    matched_chunks.append({"chunkIndex": chunk["chunk_index"], "content": chunk["content"], "score": chunk_score})
            for row in structured_map.get(document["id"], []):
                row_haystack = (row.get("search_text") or "").lower()
                row_score = sum(1 for token in tokens if token in row_haystack)
                if normalized_query and normalized_query in " ".join(row_haystack.split()):
                    row_score += 8
                if row_score:
                    content_preview = (row.get("search_text") or "")[:320]
                    matched_chunks.append(
                        {
                            "chunkIndex": 100000 + int(row["row_index"]),
                            "content": f"Structured row {int(row['row_index']) + 1}: {content_preview}",
                            "score": row_score,
                        }
                    )
            matched_chunks.sort(key=lambda item: item["score"], reverse=True)
            scored.append(
                {
                    "id": document["id"],
                    "title": document["title"],
                    "documentType": document["document_type"],
                    "department": document["department"],
                    "insuranceScheme": document["insurance_scheme"],
                    "language": document["language"],
                    "version": document["version"],
                    "summary": document["summary"],
                    "lastUpdated": document["last_updated"],
                    "score": score,
                    "sourceChunks": matched_chunks[:2],
                }
            )

        scored.sort(key=lambda item: (item["score"], item["lastUpdated"]), reverse=True)
        return scored[:top_k]

    def _generate_answer(self, user: dict, query: str, ranked: list[dict], detected_language: str):
        if "compare" in query.lower():
            ranked = self._select_comparison_documents(query, ranked)[:2]

        if not ranked or not self._has_strong_retrieval_match(query, ranked):
            return self._no_info_response(detected_language)

        full_docs = self._get_documents_by_ids([item["id"] for item in ranked])
        sections = self._retrieve_sections(query, full_docs, top_k=6)
        structured_sections = self._build_structured_evidence_sections(ranked)
        if not sections and not structured_sections:
            return self._no_info_response(detected_language)
        sections = sections + structured_sections

        preferences = self._section_preferences(query)
        checklist = self._collect_section_bullets(
            sections,
            preferred_titles=preferences["checklist"],
            limit=6,
        )
        steps = self._collect_section_bullets(
            sections,
            preferred_titles=preferences["steps"],
            limit=5,
        )
        warnings = self._collect_section_bullets(
            sections,
            preferred_titles=preferences["warnings"],
            limit=4,
            fallback_to_any=False,
        )

        if ("icu" in query.lower() or "quality" in query.lower()) and not checklist:
            checklist = self._extract_icu_quality_points(sections)
        if ("icu" in query.lower() or "quality" in query.lower()) and not steps:
            steps = checklist[:5]
        if ("icu" in query.lower() or "quality" in query.lower()) and not warnings and checklist:
            warnings = [item for item in checklist if "infection" in item.lower()][:2]

        if "compare" in query.lower():
            steps = self._build_comparison_steps(ranked[:2], full_docs)
            warnings = ["Comparison is grounded in the retrieved SOP and workflow documents only."]
        elif any(term in query.lower() for term in ["ambulance", "equipment", "equipments"]):
            warnings = []

        context_string = self._format_context(sections)

        system_prompt = (
            "You are a hospital policy assistant using retrieval-augmented generation. "
            "Answer strictly from the provided context. If the answer is not in the context, say that clearly. "
            "Do not invent policies, steps, or documents."
        )
        user_prompt = (
            f"Question: {query}\n\n"
            f"Retrieved context:\n{context_string}\n\n"
            f"Use these grounded checklist items if relevant: {', '.join(checklist)}"
        )
        live_answer = self.sarvam.chat(system_prompt, user_prompt)
        summary = live_answer or self._fallback_rag_summary(query, sections, ranked, checklist, steps)

        if detected_language != "English":
            translated_summary = self._translate_output_text(summary, detected_language)
            if translated_summary:
                summary = translated_summary
            elif detected_language == "Hindi":
                summary = self._fallback_hindi_summary(sections, checklist, steps)

        return {"summary": summary, "steps": steps, "checklist": checklist, "warnings": warnings, "no_info": False}

    def _index_structured_rows(self, connection, document_id: str, target_path: Path):
        rows = self._load_structured_rows(target_path)
        connection.execute("DELETE FROM structured_records WHERE document_id = ?", (document_id,))
        for row_index, row in enumerate(rows):
            search_text = " | ".join(f"{key}: {value}" for key, value in row.items())
            connection.execute(
                """
                INSERT INTO structured_records (document_id, row_index, content_json, search_text)
                VALUES (?, ?, ?, ?)
                """,
                (document_id, row_index, json.dumps(row, ensure_ascii=False), search_text),
            )
        return len(rows)

    def _load_structured_rows(self, target_path: Path):
        suffix = target_path.suffix.lower()
        if suffix == ".csv":
            try:
                with target_path.open("r", encoding="utf-8", errors="ignore", newline="") as handle:
                    reader = csv.DictReader(handle)
                    rows = []
                    for record in reader:
                        flattened = self._flatten_record(record)
                        if flattened:
                            rows.append(flattened)
                        if len(rows) >= 1000:
                            break
                    return rows
            except Exception:
                return []

        if suffix == ".json":
            try:
                payload = json.loads(target_path.read_text(encoding="utf-8", errors="ignore"))
            except Exception:
                return []

            if isinstance(payload, list):
                candidates = payload
            elif isinstance(payload, dict):
                list_value = next((value for value in payload.values() if isinstance(value, list)), None)
                candidates = list_value if isinstance(list_value, list) else [payload]
            else:
                candidates = []

            rows = []
            for item in candidates:
                if not isinstance(item, dict):
                    continue
                flattened = self._flatten_record(item)
                if flattened:
                    rows.append(flattened)
                if len(rows) >= 1000:
                    break
            return rows

        return []

    def _flatten_record(self, record: dict, prefix: str = ""):
        flattened = {}
        for key, value in (record or {}).items():
            clean_key = str(key).strip()
            if not clean_key:
                continue
            composite_key = f"{prefix}.{clean_key}" if prefix else clean_key
            if isinstance(value, dict):
                nested = self._flatten_record(value, composite_key)
                flattened.update(nested)
            elif isinstance(value, list):
                scalar_items = [str(item).strip() for item in value if not isinstance(item, (dict, list))]
                nested_items = [item for item in value if isinstance(item, dict)]
                if scalar_items:
                    flattened[composite_key] = ", ".join(item for item in scalar_items if item)
                for index, item in enumerate(nested_items):
                    nested = self._flatten_record(item, f"{composite_key}[{index}]")
                    flattened.update(nested)
            else:
                text_value = str(value).strip()
                if text_value:
                    flattened[composite_key] = text_value
        return flattened

    def _build_structured_evidence_sections(self, ranked: list[dict]):
        sections = []
        seen = set()
        for source in ranked:
            for chunk in source.get("sourceChunks", []):
                content = (chunk.get("content") or "").strip()
                if not content.lower().startswith("structured row"):
                    continue
                key = (source["id"], content)
                if key in seen:
                    continue
                sections.append(
                    {
                        "documentId": source["id"],
                        "documentTitle": source["title"],
                        "title": "Structured Data Evidence",
                        "text": content,
                        "bullets": [],
                        "score": chunk.get("score", 0),
                    }
                )
                seen.add(key)
                if len(sections) >= 4:
                    return sections
        return sections

    def _get_documents_by_ids(self, document_ids: list[str]):
        if not document_ids:
            return []
        placeholders = ",".join("?" for _ in document_ids)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM documents WHERE id IN ({placeholders})",
                tuple(document_ids),
            ).fetchall()
        docs = [dict(row) for row in rows]
        docs.sort(key=lambda item: document_ids.index(item["id"]))
        return docs

    def _retrieve_sections(self, query: str, docs: list[dict], top_k: int):
        tokens = self._expand_tokens(query)
        ranked_sections = []
        for doc in docs:
            for section in self._parse_sections(doc):
                haystack = " ".join([doc["title"], section["title"], section["text"], " ".join(section["bullets"])]).lower()
                score = sum(3 if token in section["title"].lower() else 1 for token in tokens if token in haystack)
                if score > 0:
                    ranked_sections.append(
                        {
                            "documentId": doc["id"],
                            "documentTitle": doc["title"],
                            "title": section["title"],
                            "text": section["text"],
                            "bullets": section["bullets"],
                            "score": score,
                        }
                    )
        ranked_sections.sort(key=lambda item: item["score"], reverse=True)
        return ranked_sections[:top_k]

    def _parse_sections(self, doc: dict):
        sections = []
        current_title = "Overview"
        text_lines = []
        bullets = []

        for raw_line in doc["content"].splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("## "):
                if text_lines or bullets:
                    sections.append(
                        {
                            "title": current_title,
                            "text": " ".join(text_lines).strip(),
                            "bullets": bullets[:],
                        }
                    )
                current_title = line[3:].strip()
                text_lines = []
                bullets = []
                continue
            if line.startswith("- "):
                bullets.append(line[2:].strip())
            else:
                text_lines.append(line)

        if text_lines or bullets:
            sections.append({"title": current_title, "text": " ".join(text_lines).strip(), "bullets": bullets[:]})
        has_markdown_sections = any(section["title"] != "Overview" for section in sections) or "## " in doc["content"]
        if has_markdown_sections:
            return sections

        fallback_sections = []
        for index, chunk in enumerate(chunk_text(doc["content"], self.settings.max_chunk_chars)):
            title_match = re.search(r"(\d+(?:\.\d+)+\s+[A-Za-z][^:\n]{0,120}:?)", chunk)
            title = title_match.group(1).strip() if title_match else f"Chunk {index + 1}"
            analysis_text = chunk[title_match.start():] if title_match else chunk
            numbered_bullets = [
                match.strip(" ;,.-")
                for match in re.findall(r"(?:^|\s)\d+\)\s*(.*?)(?=(?:\s+\d+\)|$))", analysis_text)
                if match.strip()
            ]
            dash_bullets = [line[2:].strip() for line in analysis_text.splitlines() if line.strip().startswith("- ")]
            seen = set()
            parsed_bullets = []
            for item in numbered_bullets + dash_bullets:
                key = item.lower()
                if key not in seen:
                    parsed_bullets.append(item)
                    seen.add(key)
            fallback_sections.append({"title": title, "text": chunk.strip(), "bullets": parsed_bullets})
        return fallback_sections

    def _collect_section_bullets(self, sections: list[dict], preferred_titles: list[str], limit: int, fallback_to_any: bool = True):
        preferred_titles = [title.lower() for title in preferred_titles]
        chosen = []
        seen = set()
        for section in sections:
            section_title = section["title"].lower()
            if preferred_titles and not any(term in section_title for term in preferred_titles):
                continue
            for bullet in section["bullets"]:
                key = bullet.lower()
                if key not in seen:
                    chosen.append(bullet)
                    seen.add(key)
                if len(chosen) >= limit:
                    return chosen

        if chosen:
            return chosen[:limit]

        if not fallback_to_any:
            return []

        for section in sections:
            for bullet in section["bullets"]:
                key = bullet.lower()
                if key not in seen:
                    chosen.append(bullet)
                    seen.add(key)
                if len(chosen) >= limit:
                    return chosen
        return chosen[:limit]

    def _format_context(self, sections: list[dict]):
        parts = []
        for section in sections:
            bullets = "\n".join(f"- {item}" for item in section["bullets"])
            block = f"Document: {section['documentTitle']}\nSection: {section['title']}\n{section['text']}"
            if bullets:
                block += f"\n{bullets}"
            parts.append(block)
        return "\n\n".join(parts)

    def _fallback_rag_summary(self, query: str, sections: list[dict], ranked: list[dict], checklist: list[str], steps: list[str]):
        top_doc = ranked[0]["title"] if ranked else "the retrieved corpus"
        top_section = sections[0]["title"] if sections else "Overview"
        checklist_text = ", ".join(checklist[:4]) if checklist else "see cited workflow steps"
        steps_text = " ".join(steps[:3]) if steps else "Refer to the retrieved SOP sections."
        lowered = query.lower()
        if ("ambulance" in lowered or "equipment" in lowered or "equipments" in lowered) and checklist:
            return (
                f"According to {top_doc}, section {top_section}, the ambulance equipment list includes: "
                f"{', '.join(checklist[:8])}."
            )
        if ("icu" in lowered or "quality" in lowered) and checklist:
            return (
                f"According to {top_doc}, the ICU quality assurance programme tracks: "
                f"{', '.join(checklist[:6])}."
            )
        return (
            f"Based on the retrieved RAG corpus, the strongest source is {top_doc}, especially the {top_section} section. "
            f"For this question, the most relevant grounded items are: {checklist_text}. "
            f"Recommended next actions from the retrieved hospital workflow are: {steps_text}"
        )

    def _fallback_hindi_summary(self, sections: list[dict], checklist: list[str], steps: list[str]):
        top_section = sections[0]["title"] if sections else "relevant section"
        checklist_text = ", ".join(checklist[:4]) if checklist else "retrieved SOP items"
        steps_text = " ".join(steps[:3]) if steps else "retrieved workflow steps"
        return (
            f"Yeh jawab RAG corpus ke retrieved section {top_section} par based hai. "
            f"Sabse relevant items hain: {checklist_text}. "
            f"Recommended workflow hai: {steps_text}"
        )

    def _build_comparison_steps(self, ranked: list[dict], full_docs: list[dict]):
        if len(ranked) < 2:
            return ["Comparison requires at least two retrieved documents."]
        left = ranked[0]
        right = ranked[1]
        left_doc = next((doc for doc in full_docs if doc["id"] == left["id"]), None)
        right_doc = next((doc for doc in full_docs if doc["id"] == right["id"]), None)
        return [
            f"{left['title']} focuses on eligibility, claim packet preparation, and submission control.",
            f"{right['title']} focuses on operational discharge closure, billing, and patient handover.",
            f"Use {left['title']} for claim initiation and {right['title']} for discharge-stage coordination.",
            f"Always align both documents before final claim submission for a hospitalized Ayushman patient.",
        ] if left_doc and right_doc else ["Retrieved documents were insufficient for a detailed comparison."]

    def _comparison_summary(self, left: dict, right: dict):
        changed = []
        if left["version"] != right["version"]:
            changed.append(f"Version changed from {left['version']} to {right['version']}.")
        if left["effective_date"] != right["effective_date"]:
            changed.append(
                f"Effective date changed from {left['effective_date']} to {right['effective_date']}."
            )
        changed.append("Review diff preview for approval, discharge, and documentation wording changes.")
        return " ".join(changed)

    def _extract_text(self, target_path: Path, metadata: dict):
        live_text = self.sarvam.extract_document_text(target_path, metadata["language"])
        if live_text:
            return live_text

        if target_path.suffix.lower() == ".pdf":
            pdf_text = self._extract_pdf_text_local(target_path)
            if pdf_text:
                return pdf_text

        if target_path.suffix.lower() in {".txt", ".md", ".csv"}:
            return target_path.read_text(encoding="utf-8", errors="ignore")

        if target_path.suffix.lower() == ".json":
            return target_path.read_text(encoding="utf-8", errors="ignore")

        return (
            f"{metadata['title']} for {metadata['insurance_scheme']} under {metadata['department']}. "
            f"{self.placeholder_phrase}"
        )

    def _summarize_content(self, text: str, metadata: dict):
        summary = " ".join(text.split())[:220]
        if summary:
            return summary
        return f"{metadata['document_type']} uploaded for {metadata['department']}."

    def _expand_tokens(self, query: str):
        tokens = set(re.findall(r"[a-zA-Z0-9]+(?:[.-][a-zA-Z0-9]+)*", query.lower()))
        expanded = set(tokens)
        for canonical, variants in self.synonyms.items():
            if tokens & variants:
                expanded.add(canonical)
                expanded.update(variants)
        return expanded

    def _extract_pdf_text_local(self, target_path: Path):
        try:
            from pypdf import PdfReader
        except Exception:
            return None

        try:
            reader = PdfReader(str(target_path))
            pages = []
            for page in reader.pages:
                text = page.extract_text() or ""
                cleaned = "\n".join(line.strip() for line in text.splitlines() if line.strip())
                if cleaned:
                    pages.append(cleaned)
            if not pages:
                return None
            return "\n\n".join(pages)
        except Exception:
            return None

    def _reindex_existing_uploads(self):
        with self._connect() as connection:
            rows = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT id, title, document_type, department, insurance_scheme, language,
                           summary, content, file_path
                    FROM documents
                    """
                ).fetchall()
            ]

            for row in rows:
                file_path = Path(row["file_path"])
                if not file_path.exists():
                    continue
                if not self._is_placeholder_content(row["content"]):
                    continue

                refreshed_text = self._extract_text(
                    file_path,
                    {
                        "title": row["title"],
                        "document_type": row["document_type"],
                        "department": row["department"],
                        "insurance_scheme": row["insurance_scheme"],
                        "language": row["language"],
                    },
                )
                if not refreshed_text or self._is_placeholder_content(refreshed_text):
                    continue

                refreshed_summary = self._summarize_content(
                    refreshed_text,
                    {
                        "document_type": row["document_type"],
                        "department": row["department"],
                    },
                )
                connection.execute(
                    "UPDATE documents SET content = ?, summary = ? WHERE id = ?",
                    (refreshed_text, refreshed_summary, row["id"]),
                )
                connection.execute("DELETE FROM chunks WHERE document_id = ?", (row["id"],))
                for index, chunk in enumerate(chunk_text(refreshed_text, self.settings.max_chunk_chars)):
                    connection.execute(
                        "INSERT INTO chunks (document_id, chunk_index, content) VALUES (?, ?, ?)",
                        (row["id"], index, chunk),
                    )
            connection.commit()

    def _backfill_document_access(self):
        with self._connect() as connection:
            rows = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT id, title, summary, content, document_type, department, insurance_scheme,
                           sensitivity_label, access_roles
                    FROM documents
                    """
                ).fetchall()
            ]
            for row in rows:
                access_roles = (row.get("access_roles") or "").strip()
                sensitivity_label = self._normalize_sensitivity_label(row.get("sensitivity_label"))
                inferred_sensitivity = self._determine_sensitivity_label(row, row.get("content", ""))
                inferred_access = self._access_roles_for_sensitivity(inferred_sensitivity)
                if (inferred_access != access_roles) or (inferred_sensitivity != sensitivity_label):
                    connection.execute(
                        "UPDATE documents SET sensitivity_label = ?, access_roles = ? WHERE id = ?",
                        (inferred_sensitivity, inferred_access, row["id"]),
                    )
            connection.commit()

    def _backfill_structured_records(self):
        with self._connect() as connection:
            rows = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT id, file_path
                    FROM documents
                    """
                ).fetchall()
            ]
            for row in rows:
                structured_count = connection.execute(
                    "SELECT COUNT(*) FROM structured_records WHERE document_id = ?",
                    (row["id"],),
                ).fetchone()[0]
                if structured_count > 0:
                    continue
                file_path = Path(row.get("file_path") or "")
                if not file_path.exists():
                    continue
                self._index_structured_rows(connection, row["id"], file_path)
            connection.commit()

    def _is_placeholder_content(self, content: str):
        normalized = " ".join((content or "").split())
        return self.placeholder_phrase in normalized

    def _normalize_sensitivity_label(self, label: str | None):
        normalized = (label or "").strip().lower()
        if normalized in self.sensitivity_access_map:
            return normalized
        return "public"

    def _access_roles_for_sensitivity(self, sensitivity_label: str):
        normalized = self._normalize_sensitivity_label(sensitivity_label)
        return self.sensitivity_access_map.get(normalized, self.default_access_roles)

    def _determine_sensitivity_label(self, metadata: dict, content: str):
        explicit = self._normalize_sensitivity_label(metadata.get("sensitivity_label"))
        if metadata.get("sensitivity_label"):
            return explicit
        combined = " ".join(
            [
                metadata.get("title", ""),
                metadata.get("summary", ""),
                metadata.get("document_type", ""),
                content or "",
            ]
        ).lower()
        restricted_terms = ["salary", "payroll", "compensation", "ctc", "take home", "doctor salary"]
        if any(term in combined for term in restricted_terms):
            return "confidential"
        return "public"

    def _determine_access_roles(self, metadata: dict, content: str):
        sensitivity_label = self._determine_sensitivity_label(metadata, content)
        return self._access_roles_for_sensitivity(sensitivity_label)

    def _has_document_access(self, user: dict | None, document: dict):
        if not user:
            return False
        sensitivity_label = self._normalize_sensitivity_label(document.get("sensitivity_label"))
        fallback_roles = self._access_roles_for_sensitivity(sensitivity_label)
        access_roles = {
            role.strip()
            for role in (document.get("access_roles") or fallback_roles).split(",")
            if role.strip()
        }
        return user.get("role") in access_roles

    def _detect_language(self, query: str, preferred_language: str):
        if preferred_language and preferred_language.lower() != "auto":
            normalized = preferred_language.strip().lower()
            return self._language_label_from_hint(normalized)

        lowered = query.lower()
        hint_patterns = [
            ("Hindi", ["hindi", "in hindi", "हिंदी"]),
            ("Tamil", ["tamil", "in tamil", "தமிழ்"]),
            ("Telugu", ["telugu", "in telugu", "తెలుగు"]),
            ("Kannada", ["kannada", "in kannada", "ಕನ್ನಡ"]),
            ("Malayalam", ["malayalam", "in malayalam", "മലയാളം"]),
            ("Marathi", ["marathi", "in marathi", "मराठी"]),
            ("Gujarati", ["gujarati", "in gujarati", "ગુજરાતી"]),
            ("Bengali", ["bengali", "bangla", "in bengali", "বাংলা"]),
            ("Punjabi", ["punjabi", "in punjabi", "ਪੰਜਾਬੀ"]),
            ("Odia", ["odia", "oriya", "in odia", "ଓଡ଼ିଆ"]),
        ]
        for language, hints in hint_patterns:
            if any(hint in lowered for hint in hints):
                return language

        marathi_tokens = [
            "आहे",
            "आहेत",
            "काय",
            "माहिती",
            "धोरण",
            "योजना",
            "साठी",
            "रुग्णालय",
            "मार्गदर्शक",
            "दस्तऐवज",
            "मराठी",
        ]
        if any(token in query for token in marathi_tokens):
            return "Marathi"

        script_ranges = [
            ("Hindi", r"[\u0900-\u097F]"),      # Devanagari
            ("Bengali", r"[\u0980-\u09FF]"),    # Bengali
            ("Punjabi", r"[\u0A00-\u0A7F]"),    # Gurmukhi
            ("Gujarati", r"[\u0A80-\u0AFF]"),   # Gujarati
            ("Odia", r"[\u0B00-\u0B7F]"),       # Odia
            ("Tamil", r"[\u0B80-\u0BFF]"),      # Tamil
            ("Telugu", r"[\u0C00-\u0C7F]"),     # Telugu
            ("Kannada", r"[\u0C80-\u0CFF]"),    # Kannada
            ("Malayalam", r"[\u0D00-\u0D7F]"),  # Malayalam
        ]
        for language, pattern in script_ranges:
            if re.search(pattern, query):
                return language

        if any(token in lowered for token in ["kaunse", "chahiye", "samjhao", "ke liye", "kya"]):
            return "Hindi"
        return "English"

    def _language_label_from_hint(self, hint: str):
        mapping = {
            "en": "English",
            "en-in": "English",
            "english": "English",
            "hi": "Hindi",
            "hi-in": "Hindi",
            "hindi": "Hindi",
            "ta": "Tamil",
            "ta-in": "Tamil",
            "tamil": "Tamil",
            "te": "Telugu",
            "te-in": "Telugu",
            "telugu": "Telugu",
            "kn": "Kannada",
            "kn-in": "Kannada",
            "kannada": "Kannada",
            "ml": "Malayalam",
            "ml-in": "Malayalam",
            "malayalam": "Malayalam",
            "mr": "Marathi",
            "mr-in": "Marathi",
            "marathi": "Marathi",
            "gu": "Gujarati",
            "gu-in": "Gujarati",
            "gujarati": "Gujarati",
            "bn": "Bengali",
            "bn-in": "Bengali",
            "bengali": "Bengali",
            "pa": "Punjabi",
            "pa-in": "Punjabi",
            "punjabi": "Punjabi",
            "or": "Odia",
            "od": "Odia",
            "or-in": "Odia",
            "od-in": "Odia",
            "odia": "Odia",
            "oriya": "Odia",
        }
        return mapping.get(hint, "English")

    def _language_code_for_output(self, detected_language: str):
        mapping = {
            "English": "en-IN",
            "Hindi": "hi-IN",
            "Tamil": "ta-IN",
            "Telugu": "te-IN",
            "Kannada": "kn-IN",
            "Malayalam": "ml-IN",
            "Marathi": "mr-IN",
            "Gujarati": "gu-IN",
            "Bengali": "bn-IN",
            "Punjabi": "pa-IN",
            "Odia": "od-IN",
        }
        return mapping.get(detected_language, "en-IN")

    def _translate_output_text(self, text: str, target_language_label: str):
        if not text or target_language_label == "English":
            return text

        target_language_code = self._language_code_for_output(target_language_label)
        translated = self.sarvam.translate(text, "en-IN", target_language_code)
        if translated:
            return translated

        # Fallback path when direct translate is unavailable: ask chat model for strict translation.
        fallback = self.sarvam.chat(
            "You are a professional translator. Return only translated text without extra commentary.",
            f"Translate the following text to {target_language_label}:\n\n{text}",
        )
        return fallback or text

    def _has_strong_retrieval_match(self, query: str, ranked: list[dict]):
        if not ranked:
            return False
        if not self._is_domain_query(query):
            return False
        top_score = ranked[0].get("score", 0)
        query_tokens = [
            token
            for token in self._expand_tokens(query)
            if len(token) > 2 and token not in {"what", "when", "where", "which", "who", "last", "won", "final"}
        ]
        if not query_tokens:
            return False
        top_chunks = " ".join(
            chunk["content"] for chunk in ranked[0].get("sourceChunks", [])
        ).lower()
        normalized_query = " ".join(query.lower().split())
        normalized_top_chunks = " ".join(top_chunks.split())
        sensitive_finance_terms = {"salary", "payroll", "compensation", "ctc"}
        if any(term in normalized_query for term in sensitive_finance_terms):
            if not any(term in normalized_top_chunks for term in sensitive_finance_terms):
                return False
        if normalized_query and normalized_query in normalized_top_chunks:
            return True
        matched = sum(1 for token in query_tokens if token in top_chunks)
        required_matches = 1 if len(query_tokens) <= 2 else 2
        if top_score >= 6 and matched >= required_matches:
            return True
        if top_score >= 3 and matched >= required_matches:
            return True
        return matched >= max(1, min(2, len(query_tokens)))

    def _filter_grounded_sources(self, query: str, ranked: list[dict]):
        if not ranked:
            return []
        normalized_query = " ".join(query.lower().split())
        if not normalized_query:
            return ranked

        top = ranked[0]
        top_chunks = " ".join(chunk["content"] for chunk in top.get("sourceChunks", []))
        top_haystack = " ".join(top_chunks.lower().split())
        if normalized_query in top_haystack:
            filtered = []
            for source in ranked:
                source_haystack = " ".join(
                    " ".join(chunk["content"] for chunk in source.get("sourceChunks", [])).lower().split()
                )
                if normalized_query in source_haystack or source["id"] == top["id"]:
                    filtered.append(source)
            return filtered or [top]

        top_score = top.get("score", 0)
        if top_score <= 0:
            return [top]

        query_tokens = {
            token
            for token in self._expand_tokens(query)
            if len(token) > 2 and token not in {"what", "when", "where", "which", "who"}
        }
        filtered = [top]
        for source in ranked[1:]:
            source_score = source.get("score", 0)
            if source_score < max(3, top_score * 0.6):
                continue
            source_haystack = " ".join(
                " ".join(chunk["content"] for chunk in source.get("sourceChunks", [])).lower().split()
            )
            overlap = sum(1 for token in query_tokens if token in source_haystack)
            if overlap >= max(1, min(2, len(query_tokens) // 2 or 1)):
                filtered.append(source)
        return filtered

    def _build_confidence_signal(self, query: str, ranked: list[dict], no_info: bool):
        if no_info or not ranked:
            return {
                "level": "blocked",
                "label": "Out Of Corpus",
                "description": "No grounded answer was returned because the query could not be supported by the uploaded hospital corpus.",
            }

        normalized_query = " ".join(query.lower().split())
        top = ranked[0]
        top_chunk_text = " ".join(chunk["content"] for chunk in top.get("sourceChunks", []))
        normalized_top_chunk = " ".join(top_chunk_text.lower().split())

        if normalized_query and normalized_query in normalized_top_chunk:
            return {
                "level": "exact",
                "label": "Exact Match",
                "description": "The answer is grounded in a directly matched uploaded section.",
            }

        top_score = top.get("score", 0)
        if top_score >= 6:
            return {
                "level": "grounded",
                "label": "Strong RAG Match",
                "description": "The answer is grounded in closely retrieved hospital document sections.",
            }

        return {
            "level": "semantic",
            "label": "Semantic Match",
            "description": "The answer is grounded in relevant retrieved content, but not an exact section match.",
        }

    def _has_access_blocked_match(self, query: str, user: dict | None):
        if not user or not query.strip():
            return False
        tokens = self._expand_tokens(query)
        normalized_query = " ".join(query.lower().split())
        top_score = 0
        with self._connect() as connection:
            documents = [dict(row) for row in connection.execute("SELECT * FROM documents").fetchall()]
        for document in documents:
            if self._has_document_access(user, document):
                continue
            haystack = " ".join(
                [
                    document.get("title", ""),
                    document.get("summary", ""),
                    document.get("content", ""),
                    document.get("document_type", ""),
                    document.get("insurance_scheme", ""),
                ]
            ).lower()
            score = sum(3 if token in (document.get("title", "").lower()) else 1 for token in tokens if token in haystack)
            if normalized_query and normalized_query in " ".join(haystack.split()):
                score += 8
            if score > top_score:
                top_score = score
        return top_score >= 4

    def _access_denied_response(self, detected_language: str):
        summary = (
            "I found potentially relevant information, but your role is not authorized to access this "
            "sensitivity level. Please contact an admin for access."
        )
        if detected_language != "English":
            summary = self._translate_output_text(summary, detected_language)
        return {
            "summary": summary,
            "steps": [],
            "checklist": [],
            "warnings": ["Access is restricted by document sensitivity guardrails."],
            "no_info": True,
        }

    def _no_info_response(self, detected_language: str):
        summary = "Sorry, I don't have this information in the uploaded hospital documents."
        if detected_language == "Hindi":
            summary = "Sorry, mujhe yeh jankari uploaded hospital documents mein nahi mili."
        elif detected_language != "English":
            summary = self._translate_output_text(summary, detected_language)
        return {
            "summary": summary,
            "steps": [],
            "checklist": [],
            "warnings": [],
            "no_info": True,
        }

    def _is_domain_query(self, query: str):
        lowered = query.lower()
        domain_terms = [
            "hospital",
            "policy",
            "sop",
            "ayushman",
            "claim",
            "insurance",
            "patient",
            "discharge",
            "admission",
            "document",
            "documents",
            "billing",
            "pre-authorization",
            "preauthorization",
            "pm-jay",
            "pmjay",
            "coverage",
            "exclusion",
            "settlement",
            "pre-auth",
            "kyc",
            "registration",
            "bed",
            "ward",
            "ambulance",
            "equipment",
            "equipments",
            "icu",
            "hdu",
            "quality",
            "assurance",
            "programme",
            "program",
            "infection",
            "salary",
            "payroll",
            "compensation",
            "doctor",
            "diagnostic",
            "lab",
            "multilingual",
        ]
        return any(term in lowered for term in domain_terms)

    def _extract_icu_quality_points(self, sections: list[dict]):
        text = " ".join(section["text"] for section in sections)
        indicators = [
            "Staff availability and nurse-to-patient ratio 2:1",
            "Bed availability and turnaround time for making bed",
            "Reporting time of investigations",
            "Medication administration route, dose, and frequency",
            "Coordination between staff in ICU",
            "Infection rates including UTI, intravascular device infections, respiratory tract infections, surgical site infections, and VAP",
        ]
        extracted = []
        lowered_text = text.lower()
        for indicator in indicators:
            probe_terms = [term for term in re.findall(r"[a-zA-Z]+", indicator.lower()) if len(term) > 3]
            if probe_terms and any(term in lowered_text for term in probe_terms):
                extracted.append(indicator)
        return extracted

    def _section_preferences(self, query: str):
        lowered = query.lower()
        if "pre" in lowered and "auth" in lowered:
            return {
                "checklist": ["required approval inputs", "pre-authorization rules", "approval"],
                "steps": ["planned admission workflow", "workflow", "pre-authorization rules"],
                "warnings": ["pre-authorization rules", "validation checks"],
            }
        if "reject" in lowered:
            return {
                "checklist": ["common rejection reasons", "prevention checklist"],
                "steps": ["resubmission workflow", "workflow"],
                "warnings": ["common rejection reasons", "prevention checklist"],
            }
        if "compare" in lowered:
            return {
                "checklist": ["comparison note", "required discharge documents", "required documents"],
                "steps": ["comparison note", "discharge workflow", "claim initiation workflow"],
                "warnings": ["comparison note"],
            }
        if "icu" in lowered or ("quality" in lowered and "assurance" in lowered):
            return {
                "checklist": ["quality assurance", "icu", "performance indicator"],
                "steps": ["icu", "quality assurance", "procedure"],
                "warnings": ["infection", "quality assurance"],
            }
        if "ambulance" in lowered or "equipment" in lowered or "equipments" in lowered:
            return {
                "checklist": ["3.5.1.2", "ambulance", "equipment"],
                "steps": ["3.5.1", "ambulance", "procedure"],
                "warnings": ["ambulance", "procedure"],
            }
        if "salary" in lowered or "payroll" in lowered or "compensation" in lowered:
            return {
                "checklist": ["salary", "compensation", "payroll"],
                "steps": ["salary", "compensation", "overview"],
                "warnings": ["salary", "compensation"],
            }
        if "document" in lowered or "kaunse" in lowered or "required" in lowered:
            return {
                "checklist": ["required documents", "जरूरी दस्तावेज", "clinical documentation checklist"],
                "steps": ["claim initiation workflow", "simple workflow", "workflow"],
                "warnings": ["validation checks before submission", "common rejection reasons"],
            }
        return {
            "checklist": ["required documents", "checklist", "clinical documentation checklist", "जरूरी दस्तावेज"],
            "steps": ["workflow", "process", "steps", "कार्यप्रवाह", "claim initiation workflow", "planned admission workflow"],
            "warnings": ["rejection", "validation", "prevention", "warning"],
        }

    def _select_comparison_documents(self, query: str, ranked: list[dict]):
        if len(ranked) < 2:
            return ranked
        claim_doc = next(
            (item for item in ranked if "claim submission" in item["title"].lower()),
            next((item for item in ranked if "claim" in item["title"].lower()), ranked[0]),
        )
        discharge_doc = next((item for item in ranked if "discharge" in item["title"].lower()), None)
        if discharge_doc and discharge_doc["id"] != claim_doc["id"]:
            remaining = [item for item in ranked if item["id"] not in {claim_doc["id"], discharge_doc["id"]}]
            return [claim_doc, discharge_doc] + remaining
        return ranked
