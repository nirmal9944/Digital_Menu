"""
Reusable helpers for the menu app.
"""
import logging

from django.conf import settings

logger = logging.getLogger(__name__)


def translate_to_english(text):
    """
    Translate `text` to English so kitchen staff can always read special
    instructions regardless of what language the customer typed them in.
    Source language is auto-detected — no separate detection step needed,
    both backends below do detection and translation in a single call.

    Uses the official Cloud Translation API v2 if GOOGLE_TRANSLATE_API_KEY
    is configured in settings; otherwise falls back to the free, key-less
    Google endpoint via the already-installed `deep-translator` package
    (no setup required, works out of the box).

    Returns '' for empty/whitespace-only input. On any translation
    failure (network error, rate limit, malformed response, ...) logs the
    error and returns the original text unchanged — a special instruction
    the kitchen has to puzzle out in the original language is far better
    than losing it or blocking the order entirely.
    """
    if not text or not text.strip():
        return ''

    text = text.strip()

    api_key = getattr(settings, 'GOOGLE_TRANSLATE_API_KEY', '')
    try:
        translated = (
            _translate_via_cloud_api(text, api_key)
            if api_key
            else _translate_via_free_backend(text)
        )
        return translated.strip() if translated and translated.strip() else text
    except Exception:
        logger.exception(
            'translate_to_english failed for text=%r — saving original text instead', text
        )
        return text


def _translate_via_free_backend(text):
    # Local import: keeps this dependency scoped to the one place that
    # needs it, and means a missing/broken install only breaks translation
    # (caught above) rather than the whole app failing to start.
    from deep_translator import GoogleTranslator
    return GoogleTranslator(source='auto', target='en').translate(text)


def _translate_via_cloud_api(text, api_key):
    import requests

    response = requests.post(
        'https://translation.googleapis.com/language/translate/v2',
        params={'key': api_key},
        json={'q': text, 'target': 'en', 'format': 'text'},
        timeout=5,
    )
    response.raise_for_status()
    translations = response.json().get('data', {}).get('translations', [])
    return translations[0]['translatedText'] if translations else ''
