from __future__ import annotations

import json
from pathlib import Path


SUPPORTED_LANGUAGES = {"en", "zh_cn"}


class Translator:
    def __init__(self, language: str = "en", config_dir: Path = Path("config")) -> None:
        self.config_dir = config_dir
        self.language = language if language in SUPPORTED_LANGUAGES else "en"
        self.messages = self._load_messages(self.language)

    def _load_messages(self, language: str) -> dict[str, str]:
        # Fallback chain: requested language -> English -> empty dict (keys shown as-is).
        path = self.config_dir / "i18n" / f"{language}.json"
        if not path.exists() and language != "en":
            path = self.config_dir / "i18n" / "en.json"
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def t(self, key: str, **kwargs: object) -> str:
        # Returns the translation key itself when the template is missing or formatting fails,
        # so the UI always shows something rather than crashing.
        template = self.messages.get(key, key)
        try:
            return template.format(**kwargs)
        except Exception:
            return template


def get_translator(language: str, config_dir: Path) -> Translator:
    return Translator(language=language, config_dir=config_dir)
