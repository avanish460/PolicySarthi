import json
import re
import time
import urllib.error
import urllib.request

import streamlit as st


DEFAULT_BACKEND_URL = "http://localhost:5000"


def init_state() -> None:
    if "messages" not in st.session_state:
        st.session_state.messages = [
            {
                "role": "assistant",
                "content": "Hello! I am your assistant. Ask me anything.",
            }
        ]
    if "token" not in st.session_state:
        st.session_state.token = ""
    if "current_user" not in st.session_state:
        st.session_state.current_user = {}


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


def ask_backend(base_url: str, token: str, prompt: str) -> tuple[dict | None, str | None]:
    data, err = api_request(
        "POST",
        f"{base_url.rstrip('/')}/api/query",
        {"query": prompt, "language": "auto", "include_voice": False},
        token=token,
    )
    if err:
        return None, err

    if not data or not (data.get("summary") or "").strip():
        return None, "No summary text found in backend response."
    return data, None


def get_health(base_url: str) -> tuple[dict | None, str | None]:
    return api_request("GET", f"{base_url.rstrip('/')}/api/health")


def build_response_markdown(data: dict) -> str:
    text = (data.get("summary") or "").strip()
    # Remove any hidden reasoning tags if backend/model includes them.
    text = re.sub(r"<think\b[^>]*>.*?</think>", "", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"</?think\b[^>]*>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text or "No answer generated."


def stream_text(text: str):
    for i in range(0, len(text), 5):
        yield text[i : i + 5]
        time.sleep(0.01)


st.set_page_config(page_title="Policy Sarthi", page_icon=":speech_balloon:", layout="centered")
init_state()

st.markdown(
    """
    <style>
    .block-container {padding-top: 1.6rem; padding-bottom: 1.2rem; max-width: 900px;}
    .stChatMessage {border-radius: 14px;}
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("Policy Sarthi")
st.caption("Policy assistant UI integrated with backend APIs")

with st.sidebar:
    st.subheader("Connection")
    backend_url = st.text_input("Backend URL", value=DEFAULT_BACKEND_URL)
    username = st.text_input("Username", value="admin")
    password = st.text_input("Password", value="admin123", type="password")

    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("Login", use_container_width=True):
            token, err = login(backend_url, username, password)
            if err:
                st.error(err)
            else:
                st.session_state.token = token
                st.success("Logged in")
    with col2:
        if st.button("Logout", use_container_width=True):
            st.session_state.token = ""
            st.session_state.current_user = {}
            st.info("Logged out")
    with col3:
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

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

if prompt := st.chat_input("Type your message..."):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        thinking_placeholder = st.empty()
        thinking_placeholder.markdown("Thinking.......")

        if not st.session_state.token:
            token, err = login(backend_url, username, password)
            if err:
                final_text = f"Login failed: {err}. Use sidebar credentials and click Login."
            else:
                st.session_state.token = token

        if st.session_state.token:
            response_data, err = ask_backend(backend_url, st.session_state.token, prompt)
            if err:
                final_text = f"Backend error: {err}"
            else:
                final_text = build_response_markdown(response_data or {})

        thinking_placeholder.empty()
        st.write_stream(stream_text(final_text))

    st.session_state.messages.append({"role": "assistant", "content": final_text})
