import base64
from pathlib import Path


class SarvamService:
    def __init__(self, settings):
        self.settings = settings
        self.enabled = bool(settings.sarvam_api_key)
        self.client = None

        if self.enabled:
            try:
                from sarvamai import SarvamAI

                self.client = SarvamAI(api_subscription_key=settings.sarvam_api_key)
            except Exception:
                self.enabled = False
                self.client = None

    def extract_document_text(self, file_path: Path, language: str):
        if not self.enabled or not self.client:
            return None
        try:
            job = self.client.document_intelligence.create_job(
                language=language,
                output_format="md",
            )
            job.upload_file(str(file_path))
            result = job.get_result()
            return getattr(result, "output", None) or getattr(result, "markdown", None)
        except Exception:
            return None

    def translate(self, text: str, source_language: str, target_language: str):
        if not self.enabled or not self.client:
            return None
        try:
            response = self.client.text.translate(
                input=text,
                source_language_code=source_language,
                target_language_code=target_language,
            )
            return (
                getattr(response, "translated_text", None)
                or getattr(response, "translation", None)
                or getattr(response, "text", None)
            )
        except Exception:
            return None

    def chat(self, system_prompt: str, user_prompt: str):
        if not self.enabled or not self.client:
            return None
        try:
            response = self.client.chat.completions(
                model=self.settings.chat_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            return response.choices[0].message.content
        except Exception:
            return None

    def speech_to_text(self, file_path: Path):
        if not self.enabled or not self.client:
            return None
        try:
            with open(file_path, "rb") as audio_file:
                response = self.client.speech_to_text.transcribe(file=audio_file)
            return {
                "transcript": getattr(response, "transcript", ""),
                "language_code": getattr(response, "language_code", "unknown"),
            }
        except Exception:
            return None

    def text_to_speech(self, text: str, target_language: str):
        if not self.enabled or not self.client:
            return None
        try:
            response = self.client.text_to_speech.convert(
                text=text,
                target_language_code=target_language,
            )
            audio_bytes = getattr(response, "audio", None) or getattr(response, "audio_bytes", None)
            if not audio_bytes:
                return None
            if isinstance(audio_bytes, str):
                return audio_bytes
            return base64.b64encode(audio_bytes).decode("utf-8")
        except Exception:
            return None
