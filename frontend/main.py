import json
import re
import time
import urllib.error
import urllib.request
import uuid
import hashlib

import base64

import streamlit as st
import streamlit.components.v1 as components


DEFAULT_BACKEND_URL = "http://localhost:5000"


def init_state() -> None:
    if "messages" not in st.session_state:
        st.session_state.messages = [
            {
                "role": "assistant",
                "content": "Hello! I am your Policy Sathi AI assistant. Ask me anything related to hospital policies and Ayushman Bharat policy guidance.",
            }
        ]
    if "token" not in st.session_state:
        st.session_state.token = ""
    if "current_user" not in st.session_state:
        st.session_state.current_user = {}
    if "spoken_tts_message_ids" not in st.session_state:
        st.session_state.spoken_tts_message_ids = set()
    if "played_audio_message_ids" not in st.session_state:
        st.session_state.played_audio_message_ids = set()
    if "last_auto_voice_fingerprint" not in st.session_state:
        st.session_state.last_auto_voice_fingerprint = ""


def ensure_feedback_state() -> None:
    messages = st.session_state.messages
    last_user_query = ""
    saw_user_query = False
    for msg in messages:
        if msg.get("role") == "user":
            last_user_query = (msg.get("content") or "").strip()
            if last_user_query:
                saw_user_query = True
            continue

        if msg.get("role") != "assistant":
            continue

        content = (msg.get("content") or "").strip()
        if not content:
            continue
        if not saw_user_query:
            continue

        msg.setdefault("feedback_enabled", True)
        msg.setdefault("feedback_state", "")
        msg.setdefault("feedback_query", last_user_query)
        msg.setdefault("feedback_query_log_id", None)
        msg.setdefault("feedback_top_document", "")
        msg.setdefault("feedback_top_document_id", "")
        msg.setdefault("feedback_form_open", False)
        msg.setdefault("feedback_comment", "")
        msg.setdefault("feedback_correction", "")


def api_request(
    method: str,
    url: str,
    payload: dict | None = None,
    token: str | None = None,
) -> tuple[dict | None, str | None]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8")), None
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        try:
            parsed = json.loads(body)
            return None, parsed.get("error") or f"HTTP {e.code}"
        except json.JSONDecodeError:
            return None, f"HTTP {e.code}: {body[:200]}"
    except Exception as e:
        return None, str(e)


def api_multipart_post(
    url: str,
    token: str | None = None,
    fields: dict[str, str] | None = None,
    file_field_name: str | None = None,
    file_name: str | None = None,
    file_bytes: bytes | None = None,
    file_content_type: str = "application/octet-stream",
) -> tuple[dict | None, str | None]:
    boundary = f"----PolicySarthiBoundary{uuid.uuid4().hex}"
    body = bytearray()
    parts = fields or {}

    for key, value in parts.items():
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("utf-8"))
        body.extend(str(value).encode("utf-8"))
        body.extend(b"\r\n")

    if file_field_name and file_name and file_bytes is not None:
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(
            (
                f'Content-Disposition: form-data; name="{file_field_name}"; '
                f'filename="{file_name}"\r\n'
            ).encode("utf-8")
        )
        body.extend(f"Content-Type: {file_content_type}\r\n\r\n".encode("utf-8"))
        body.extend(file_bytes)
        body.extend(b"\r\n")

    body.extend(f"--{boundary}--\r\n".encode("utf-8"))

    headers = {
        "Content-Type": f"multipart/form-data; boundary={boundary}",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, data=bytes(body), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8")), None
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="ignore")
        try:
            parsed = json.loads(body_text)
            return None, parsed.get("error") or f"HTTP {e.code}"
        except json.JSONDecodeError:
            return None, f"HTTP {e.code}: {body_text[:200]}"
    except Exception as e:
        return None, str(e)


def login(base_url: str, username: str, password: str) -> tuple[str | None, str | None]:
    data, err = api_request(
        "POST",
        f"{base_url.rstrip('/')}/api/auth/login",
        {"username": username, "password": password},
    )
    if err:
        return None, err

    token = (data or {}).get("token")
    if not token:
        return None, "Login succeeded but no token was returned."

    st.session_state.current_user = (data or {}).get("user") or {}
    return token, None


def ask_backend(
    base_url: str,
    token: str,
    prompt: str,
    language: str = "auto",
    include_voice: bool = False,
) -> tuple[dict | None, str | None]:
    data, err = api_request(
        "POST",
        f"{base_url.rstrip('/')}/api/query",
        {"query": prompt, "language": language, "include_voice": include_voice},
        token=token,
    )
    if err:
        return None, err

    if not data or not (data.get("summary") or "").strip():
        return None, "No summary text found in backend response."
    return data, None


def get_health(base_url: str) -> tuple[dict | None, str | None]:
    return api_request("GET", f"{base_url.rstrip('/')}/api/health")


def transcribe_voice(
    base_url: str, token: str, audio_file
) -> tuple[str | None, str | None, str | None]:
    file_name = getattr(audio_file, "name", "") or "voice.wav"
    file_content_type = getattr(audio_file, "type", "") or "audio/wav"
    file_bytes = audio_file.getvalue()
    data, err = api_multipart_post(
        f"{base_url.rstrip('/')}/api/voice/transcribe",
        token=token,
        fields={},
        file_field_name="file",
        file_name=file_name,
        file_bytes=file_bytes,
        file_content_type=file_content_type,
    )
    if err:
        return None, None, err
    transcript = ((data or {}).get("transcript") or "").strip()
    language_code = ((data or {}).get("languageCode") or "auto").strip()
    if not transcript:
        return None, None, "No transcript returned from voice API."
    return transcript, language_code, None


def normalize_query_language(language_code: str | None) -> str:
    normalized = (language_code or "").strip().lower()
    language_map = {
        "en": "en-IN",
        "en-in": "en-IN",
        "hi": "hi-IN",
        "hi-in": "hi-IN",
        "ta": "ta-IN",
        "ta-in": "ta-IN",
        "te": "te-IN",
        "te-in": "te-IN",
        "kn": "kn-IN",
        "kn-in": "kn-IN",
        "ml": "ml-IN",
        "ml-in": "ml-IN",
        "mr": "mr-IN",
        "mr-in": "mr-IN",
        "gu": "gu-IN",
        "gu-in": "gu-IN",
        "bn": "bn-IN",
        "bn-in": "bn-IN",
        "pa": "pa-IN",
        "pa-in": "pa-IN",
        "od": "od-IN",
        "od-in": "od-IN",
        "or": "od-IN",
        "or-in": "od-IN",
    }
    return language_map.get(normalized, "auto")


def upload_policy_document(
    base_url: str,
    token: str,
    upload,
    title: str,
    document_type: str,
    department: str,
    insurance_scheme: str,
    effective_date: str,
    language: str,
    version: str,
    summary: str,
    sensitivity_label: str,
) -> tuple[dict | None, str | None]:
    file_name = getattr(upload, "name", "") or "policy-upload.bin"
    file_content_type = getattr(upload, "type", "") or "application/octet-stream"
    file_bytes = upload.getvalue()
    data, err = api_multipart_post(
        f"{base_url.rstrip('/')}/api/documents/upload",
        token=token,
        fields={
            "title": title,
            "document_type": document_type,
            "department": department,
            "insurance_scheme": insurance_scheme,
            "effective_date": effective_date,
            "language": language,
            "version": version,
            "summary": summary,
            "sensitivity_label": sensitivity_label,
        },
        file_field_name="file",
        file_name=file_name,
        file_bytes=file_bytes,
        file_content_type=file_content_type,
    )
    return data, err


def submit_feedback(
    base_url: str,
    token: str,
    query: str,
    rating: int,
    query_log_id: int | None = None,
    top_document: str = "",
    top_document_id: str = "",
    comment: str = "",
    correction: str = "",
) -> tuple[dict | None, str | None]:
    payload = {
        "query": query,
        "rating": rating,
        "queryLogId": query_log_id,
        "topDocument": top_document,
        "topDocumentId": top_document_id,
        "comment": comment,
        "correction": correction,
    }
    return api_request(
        "POST",
        f"{base_url.rstrip('/')}/api/feedback",
        payload=payload,
        token=token,
    )


def build_response_markdown(data: dict) -> str:
    text = (data.get("summary") or "").strip()
    # Remove any hidden reasoning tags if backend/model includes them.
    text = re.sub(r"<think\b[^>]*>.*?</think>", "", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"</?think\b[^>]*>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text or "No answer generated."


def extract_voice_bytes(data: dict | None) -> bytes | None:
    voice_payload = (data or {}).get("voicePlayback") or {}
    encoded_audio = (voice_payload.get("audioBase64") or "").strip()
    if not encoded_audio:
        return None
    try:
        return base64.b64decode(encoded_audio)
    except Exception:
        return None


def infer_output_language_code(data: dict | None) -> str:
    voice_payload = (data or {}).get("voicePlayback") or {}
    voice_lang = (voice_payload.get("language") or "").strip()
    if voice_lang:
        return voice_lang
    detected = ((data or {}).get("detectedLanguage") or "").strip().lower()
    language_map = {
        "hindi": "hi-IN",
        "tamil": "ta-IN",
        "telugu": "te-IN",
        "kannada": "kn-IN",
        "malayalam": "ml-IN",
        "marathi": "mr-IN",
        "gujarati": "gu-IN",
        "bengali": "bn-IN",
        "punjabi": "pa-IN",
        "odia": "od-IN",
        "english": "en-IN",
    }
    return language_map.get(detected, "en-IN")


def render_audio_player(audio_bytes: bytes, player_id: str, autoplay: bool = False) -> None:
    if not audio_bytes:
        return
    encoded_audio = base64.b64encode(audio_bytes).decode("ascii")
    components.html(
        f"""
        <div style="display:flex; align-items:center; gap:8px; margin: 0.2rem 0 0.25rem 0;">
          <button
            id="ps-audio-btn-{player_id}"
            title="Play/Pause"
            style="
              width:40px;
              height:40px;
              border-radius:999px;
              border:1px solid rgba(255,255,255,0.25);
              background:#0f7cc9;
              color:#ffffff;
              font-size:16px;
              line-height:1;
              cursor:pointer;
              display:flex;
              align-items:center;
              justify-content:center;
            "
          >
            ▶
          </button>
        </div>
        <audio id="{player_id}" preload="auto" style="display:none;">
          <source src="data:audio/wav;base64,{encoded_audio}" type="audio/wav" />
        </audio>
        <script>
        (function() {{
          const audio = document.getElementById("{player_id}");
          const btn = document.getElementById("ps-audio-btn-{player_id}");
          if (!audio) return;
          if (!btn) return;

          function setIcon() {{
            btn.textContent = audio.paused ? "▶" : "❚❚";
          }}

          btn.onclick = function () {{
            if (audio.paused) {{
              audio.play().catch(() => {{}});
            }} else {{
              audio.pause();
            }}
          }};

          audio.addEventListener("play", setIcon);
          audio.addEventListener("pause", setIcon);
          audio.addEventListener("ended", setIcon);

          setIcon();
          {"audio.play().then(setIcon).catch(() => {});" if autoplay else ""}
        }})();
        </script>
        """,
        height=52,
    )


def render_browser_tts_controls(
    message_id: str, text: str, language_code: str, autoplay: bool = False
) -> None:
    if not message_id or not (text or "").strip():
        return

    spoken_ids = st.session_state.spoken_tts_message_ids
    should_autoplay = autoplay and (message_id not in spoken_ids)
    if should_autoplay:
        spoken_ids.add(message_id)

    payload = json.dumps(
        {
            "messageId": message_id,
            "text": text,
            "languageCode": language_code or "en-IN",
            "autoplay": should_autoplay,
        }
    )
    components.html(
        f"""
        <div style="display:flex; gap:8px; align-items:center; margin: 0.15rem 0 0.25rem 0;">
          <button id="ps-play-{message_id}" style="padding: 0.25rem 0.55rem;">Play</button>
          <button id="ps-pause-{message_id}" style="padding: 0.25rem 0.55rem;">Pause</button>
          <span style="font-size: 0.8rem; opacity: 0.75;">Voice controls</span>
        </div>
        <script>
        (function() {{
          const payload = {payload};
          const id = payload.messageId;
          const text = (payload.text || "").trim();
          const lang = payload.languageCode || "en-IN";
          if (!text) return;

          const playButton = document.getElementById(`ps-play-${{id}}`);
          const pauseButton = document.getElementById(`ps-pause-${{id}}`);

          function speakFromStart() {{
            window.speechSynthesis.cancel();
            const utterance = new SpeechSynthesisUtterance(text);
            utterance.lang = lang;
            utterance.rate = 1.0;
            utterance.pitch = 1.0;
            window.__policySarthiUtterance = utterance;
            window.__policySarthiUtteranceId = id;
            window.speechSynthesis.speak(utterance);
          }}

          if (playButton) {{
            playButton.onclick = function () {{
              if (window.speechSynthesis.paused) {{
                window.speechSynthesis.resume();
                return;
              }}
              if (window.speechSynthesis.speaking && window.__policySarthiUtteranceId === id) {{
                return;
              }}
              speakFromStart();
            }};
          }}

          if (pauseButton) {{
            pauseButton.onclick = function () {{
              if (window.speechSynthesis.speaking) {{
                window.speechSynthesis.pause();
              }}
            }};
          }}

          if (payload.autoplay) {{
            speakFromStart();
          }}
        }})();
        </script>
        """,
        height=48,
    )


def stream_text(text: str):
    for i in range(0, len(text), 5):
        yield text[i : i + 5]
        time.sleep(0.01)


st.set_page_config(page_title="Policy Sarthi", page_icon=":speech_balloon:", layout="centered")
init_state()
ensure_feedback_state()

st.markdown(
    """
    <style>
    .block-container {padding-top: 1.6rem; padding-bottom: 1.2rem; max-width: 900px;}
    .stChatMessage {border-radius: 14px;}
    .voice-input-card {
        border: 1px solid rgba(49, 51, 63, 0.2);
        border-radius: 14px;
        padding: 0.9rem 1rem 0.6rem 1rem;
        margin-bottom: 0.7rem;
        background: linear-gradient(120deg, rgba(220, 241, 255, 0.45), rgba(238, 248, 255, 0.35));
    }
    .voice-input-head {
        display: flex;
        align-items: center;
        gap: 0.55rem;
        margin-bottom: 0.45rem;
    }
    .voice-input-icon {
        width: 34px;
        height: 34px;
        border-radius: 999px;
        display: flex;
        align-items: center;
        justify-content: center;
        background: #0f7cc9;
        color: #ffffff;
        font-size: 1rem;
        line-height: 1;
        flex-shrink: 0;
    }
    .voice-input-title {
        font-weight: 600;
        font-size: 0.98rem;
        line-height: 1.2;
    }
    .voice-input-subtitle {
        font-size: 0.82rem;
        opacity: 0.82;
        margin-top: 0.1rem;
    }
    div[data-testid="stTextInput"] [data-baseweb="input"] {
        min-height: 44px;
        height: 44px;
    }
    div[data-testid="stTextInput"] input {
        min-height: 44px;
        height: 44px;
        line-height: 44px;
    }
    div[data-testid="stButton"] > button {
        min-height: 44px;
        height: 44px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("Policy Sarthi")
st.caption(
    "AI assistant for hospital SOPs and Ayushman Bharat policy guidance: ask questions, upload policies (admin only), and get grounded answers."
)

with st.sidebar:
    with st.expander("Login & Connection", expanded=False):
        backend_url = st.text_input("Backend URL", value=DEFAULT_BACKEND_URL)
        username = st.text_input("Username", value="admin")
        password = st.text_input("Password", value="admin123", type="password")

        row1_col1, row1_col2 = st.columns(2)
        with row1_col1:
            if st.button("Login", use_container_width=True):
                token, err = login(backend_url, username, password)
                if err:
                    st.error(err)
                else:
                    st.session_state.token = token
                    st.success("Logged in")
        with row1_col2:
            if st.button("Logout", use_container_width=True):
                st.session_state.token = ""
                st.session_state.current_user = {}
                st.info("Logged out")

        if st.button("Health", use_container_width=True):
            health, err = get_health(backend_url)
            if err:
                st.error(f"Backend unreachable: {err}")
            else:
                st.success(f"Status: {health.get('status', 'ok')}")

    st.divider()
    if st.button("Clear Chat", use_container_width=True):
        st.session_state.messages = [
            {"role": "assistant", "content": "New chat started. How can I help you?"}
        ]
        st.rerun()

    mode = "Connected" if st.session_state.token else "Not authenticated"
    st.caption(f"Current: {mode}")
    user = st.session_state.current_user
    if user:
        display_name = user.get("displayName", user.get("username", "-"))
        role = user.get("role", "-")
        st.caption(f"User: {display_name} ({role})")

    st.divider()
    is_admin = (user or {}).get("role") == "admin"
    if is_admin and st.session_state.token:
        with st.expander("Upload Policy (Admin)", expanded=False):
            with st.form("admin_upload_form", clear_on_submit=True):
                upload_file = st.file_uploader(
                    "Upload policy document",
                    type=["pdf", "docx", "txt", "md", "csv", "json"],
                    accept_multiple_files=False,
                )
                upload_title = st.text_input("Title", value="")
                upload_scheme = st.text_input("Policy/Scheme Category", value="")
                col_a, col_b = st.columns(2)
                with col_a:
                    upload_type = st.text_input("Document Type", value="Policy")
                    upload_department = st.text_input("Department", value="Administration")
                with col_b:
                    upload_effective_date = st.text_input("Effective Date (YYYY-MM-DD)", value="")
                    upload_version = st.text_input("Version", value="v1")
                upload_sensitivity = st.selectbox(
                    "Sensitivity Label",
                    options=["public", "internal", "confidential", "restricted"],
                    index=1,
                    help="Controls which roles can access this document data.",
                )
                upload_summary = st.text_area("Summary (optional)", value="", height=80)
                upload_language = st.text_input(
                    "Document Language (Hindi / English / Any Native Language)",
                    value="auto",
                )
                upload_submit = st.form_submit_button("Upload", use_container_width=True)

            if upload_submit:
                if not upload_file:
                    st.error("Please select a policy document to upload.")
                else:
                    result, err = upload_policy_document(
                        backend_url,
                        st.session_state.token,
                        upload_file,
                        (upload_title or "").strip(),
                        (upload_type or "").strip() or "Policy",
                        (upload_department or "").strip() or "Administration",
                        (upload_scheme or "").strip() or "General Hospital Policy",
                        (upload_effective_date or "").strip(),
                        (upload_language or "").strip() or "auto",
                        (upload_version or "").strip() or "v1",
                        (upload_summary or "").strip(),
                        (upload_sensitivity or "internal").strip().lower(),
                    )
                    if err:
                        st.error(f"Upload failed: {err}")
                    else:
                        st.success(
                            f"Uploaded and indexed: {((result or {}).get('documentId') or 'Document')}"
                        )
    else:
        st.caption("Policy upload is visible only for admin users.")

for idx, msg in enumerate(st.session_state.messages):
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("audio_bytes"):
            autoplay_audio = False
            message_id = (msg.get("message_id") or "").strip()
            if msg.get("autoplay_audio") and message_id:
                played_ids = st.session_state.played_audio_message_ids
                if message_id not in played_ids:
                    autoplay_audio = True
                    played_ids.add(message_id)
            render_audio_player(
                msg["audio_bytes"],
                player_id=f"history-audio-{idx}",
                autoplay=autoplay_audio,
            )
        if msg["role"] == "assistant" and msg.get("feedback_enabled"):
            feedback_state = msg.get("feedback_state", "")
            if feedback_state == "up":
                st.caption("Feedback received: thumbs up")
            elif feedback_state == "down":
                st.caption("Feedback received: thumbs down")
                if msg.get("feedback_comment"):
                    st.caption(f"Comment: {msg.get('feedback_comment')}")
                if msg.get("feedback_correction"):
                    st.caption(f"Correction: {msg.get('feedback_correction')}")
            else:
                up_col, down_col = st.columns(2)
                with up_col:
                    thumbs_up = st.button(
                        "👍 Helpful",
                        key=f"feedback_up_{idx}",
                        use_container_width=True,
                    )
                with down_col:
                    thumbs_down = st.button(
                        "👎 Not Helpful",
                        key=f"feedback_down_{idx}",
                        use_container_width=True,
                    )
                if thumbs_up or thumbs_down:
                    rating = 1 if thumbs_up else -1
                    if not st.session_state.token:
                        token, err = login(backend_url, username, password)
                        if err:
                            st.error(f"Login failed: {err}")
                        else:
                            st.session_state.token = token
                    if st.session_state.token:
                        result, err = submit_feedback(
                            base_url=backend_url,
                            token=st.session_state.token,
                            query=msg.get("feedback_query", ""),
                            rating=rating,
                            query_log_id=msg.get("feedback_query_log_id"),
                            top_document=msg.get("feedback_top_document", ""),
                            top_document_id=msg.get("feedback_top_document_id", ""),
                        )
                        if err:
                            st.error(f"Feedback error: {err}")
                        else:
                            st.session_state.messages[idx]["feedback_state"] = "up" if rating > 0 else "down"
                            st.session_state.messages[idx]["feedback_comment"] = ""
                            st.session_state.messages[idx]["feedback_correction"] = ""
                            st.success((result or {}).get("message", "Feedback submitted"))
                            st.rerun()

                if st.button(
                    "👎 Not Helpful + Comment",
                    key=f"feedback_open_form_{idx}",
                    use_container_width=True,
                ):
                    st.session_state.messages[idx]["feedback_form_open"] = True
                    st.rerun()

                if msg.get("feedback_form_open"):
                    with st.form(f"feedback_down_form_{idx}", clear_on_submit=True):
                        down_comment = st.text_area(
                            "What was not helpful? (optional)",
                            value="",
                            height=70,
                        )
                        down_correction = st.text_area(
                            "Suggested correction (optional)",
                            value="",
                            height=90,
                        )
                        submit_down_feedback = st.form_submit_button(
                            "Submit Negative Feedback",
                            use_container_width=True,
                        )

                    if submit_down_feedback:
                        if not st.session_state.token:
                            token, err = login(backend_url, username, password)
                            if err:
                                st.error(f"Login failed: {err}")
                            else:
                                st.session_state.token = token
                        if st.session_state.token:
                            result, err = submit_feedback(
                                base_url=backend_url,
                                token=st.session_state.token,
                                query=msg.get("feedback_query", ""),
                                rating=-1,
                                query_log_id=msg.get("feedback_query_log_id"),
                                top_document=msg.get("feedback_top_document", ""),
                                top_document_id=msg.get("feedback_top_document_id", ""),
                                comment=(down_comment or "").strip(),
                                correction=(down_correction or "").strip(),
                            )
                            if err:
                                st.error(f"Feedback error: {err}")
                            else:
                                st.session_state.messages[idx]["feedback_state"] = "down"
                                st.session_state.messages[idx]["feedback_form_open"] = False
                                st.session_state.messages[idx]["feedback_comment"] = (down_comment or "").strip()
                                st.session_state.messages[idx]["feedback_correction"] = (down_correction or "").strip()
                                st.success((result or {}).get("message", "Negative feedback submitted"))
                                st.rerun()

pending_prompt = None
pending_prompt_source = "text"
pending_prompt_language = "auto"
submitted = False
typed_prompt = ""
voice_audio = None

st.markdown(
    """
    <div class="voice-input-card">
        <div class="voice-input-head">
            <div class="voice-input-icon">🎙</div>
            <div>
                <div class="voice-input-title">Voice Input</div>
                <div class="voice-input-subtitle">Record your question to auto-transcribe with Sarvam STT and auto-send.</div>
            </div>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)
voice_audio = st.audio_input("Voice query", label_visibility="collapsed")

with st.form("query_form", clear_on_submit=True):
    input_col, send_col = st.columns([9, 1])
    with input_col:
        typed_prompt = st.text_input(
            "Type your message...",
            placeholder="Type your message...",
            label_visibility="collapsed",
        )
    with send_col:
        submitted = st.form_submit_button("Send", use_container_width=True)

if submitted:
    typed_prompt = (typed_prompt or "").strip()
    if typed_prompt:
        pending_prompt = typed_prompt
        pending_prompt_source = "text"
        pending_prompt_language = "auto"
        st.session_state.last_auto_voice_fingerprint = ""
    elif voice_audio is not None:
        with st.chat_message("assistant"):
            thinking_placeholder = st.empty()
            thinking_placeholder.markdown("Thinking.......")
            if not st.session_state.token:
                token, err = login(backend_url, username, password)
                if err:
                    thinking_placeholder.empty()
                    st.error(f"Login failed: {err}")
                else:
                    st.session_state.token = token

            if st.session_state.token:
                transcript, detected_language_code, err = transcribe_voice(
                    backend_url, st.session_state.token, voice_audio
                )
                thinking_placeholder.empty()
                if err:
                    st.error(f"Voice error: {err}")
                else:
                    pending_prompt = transcript
                    pending_prompt_source = "voice"
                    pending_prompt_language = normalize_query_language(detected_language_code)
                    voice_bytes = voice_audio.getvalue()
                    st.session_state.last_auto_voice_fingerprint = hashlib.sha1(voice_bytes).hexdigest()
    else:
        st.warning("Type a message or record voice in the Voice Input panel.")
elif voice_audio is not None and not (typed_prompt or "").strip():
    voice_bytes = voice_audio.getvalue()
    voice_fingerprint = hashlib.sha1(voice_bytes).hexdigest()
    if voice_fingerprint != st.session_state.last_auto_voice_fingerprint:
        with st.chat_message("assistant"):
            thinking_placeholder = st.empty()
            thinking_placeholder.markdown("Thinking.......")
            if not st.session_state.token:
                token, err = login(backend_url, username, password)
                if err:
                    thinking_placeholder.empty()
                    st.error(f"Login failed: {err}")
                else:
                    st.session_state.token = token

            if st.session_state.token:
                transcript, detected_language_code, err = transcribe_voice(
                    backend_url, st.session_state.token, voice_audio
                )
                thinking_placeholder.empty()
                if err:
                    st.error(f"Voice error: {err}")
                    st.session_state.last_auto_voice_fingerprint = voice_fingerprint
                else:
                    pending_prompt = transcript
                    pending_prompt_source = "voice"
                    pending_prompt_language = normalize_query_language(detected_language_code)
                    st.session_state.last_auto_voice_fingerprint = voice_fingerprint

if pending_prompt:
    st.session_state.messages.append({"role": "user", "content": pending_prompt})
    with st.chat_message("user"):
        st.markdown(pending_prompt)

    with st.chat_message("assistant"):
        thinking_placeholder = st.empty()
        thinking_placeholder.markdown("Thinking.......")
        final_audio_bytes = None
        response_data = None

        if not st.session_state.token:
            token, err = login(backend_url, username, password)
            if err:
                final_text = f"Login failed: {err}. Use sidebar credentials and click Login."
            else:
                st.session_state.token = token

        if st.session_state.token:
            response_data, err = ask_backend(
                backend_url,
                st.session_state.token,
                pending_prompt,
                language=pending_prompt_language,
                include_voice=(pending_prompt_source == "voice"),
            )
            if err:
                final_text = f"Backend error: {err}"
            else:
                final_text = build_response_markdown(response_data or {})
                final_audio_bytes = extract_voice_bytes(response_data)

        thinking_placeholder.empty()
        st.write_stream(stream_text(final_text))
        if pending_prompt_source == "voice" and not final_audio_bytes:
            st.caption("Voice playback unavailable from Sarvam for this response.")

    assistant_message_id = uuid.uuid4().hex

    st.session_state.messages.append(
        {
            "message_id": assistant_message_id,
            "role": "assistant",
            "content": final_text,
            "audio_bytes": final_audio_bytes,
            "autoplay_audio": bool(final_audio_bytes and pending_prompt_source == "voice"),
            "feedback_enabled": bool(response_data and response_data.get("query")),
            "feedback_state": "",
            "feedback_query": (response_data or {}).get("query", pending_prompt),
            "feedback_query_log_id": (response_data or {}).get("queryLogId"),
            "feedback_top_document": (response_data or {}).get("topDocument", ""),
            "feedback_top_document_id": (response_data or {}).get("topDocumentId", ""),
            "feedback_form_open": False,
            "feedback_comment": "",
            "feedback_correction": "",
        }
    )
    st.rerun()
