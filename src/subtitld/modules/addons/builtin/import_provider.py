"""Built-in "Import from file" provider.

Turns the old standalone "Import" button of the transcription panel into
a first-class built-in engine: it shows up in the transcription engine
picker (and the Add-ons list) alongside the real ASR engines, because
"import an existing subtitle file" is just another way to populate the
timeline with subtitles.

It is registered for the ASR task so it lands in the same engine
combobox, but it doesn't transcribe audio — the actual work is a file
dialog + parse, driven entirely by the panel widget in
``left_panel_import`` (``_ImportASRPanel``). ``transcribe()`` is therefore
a no-op: nothing calls it, since the import panel hides the "Start
transcription" button and imports through its own button.

For now this provider carries no options and no config schema — it is
"just the Import button". Future import-related settings (default
format, encoding, merge-vs-replace) would grow a ``config_schema`` here.
"""

from __future__ import annotations

from PySide6.QtCore import QObject

from subtitld.modules.addons.provider import ASRProvider


class ImportProvider(ASRProvider):
    """File-import "engine" — populates subtitles from an existing file."""

    PROVIDER_ID = 'import'

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)

    # ---- identity --------------------------------------------------------
    @property
    def id(self) -> str:  # noqa: A003
        return self.PROVIDER_ID

    @property
    def display_name(self) -> str:
        return 'Import from file'

    @property
    def is_builtin(self) -> bool:
        return True

    @property
    def languages(self) -> list[str]:
        # Language-agnostic: an imported file carries its own language.
        # Empty is fine — the engine picker lists every ASR provider
        # regardless of language.
        return []

    @property
    def config_schema(self) -> dict | None:
        return None

    # ---- synthesis -------------------------------------------------------
    def transcribe(self, audio_path: str, language: str, options: dict | None = None) -> None:
        # No-op: the import panel (`_ImportASRPanel`) does the work via its
        # own button; this engine never runs through the audio-transcribe
        # path. Present only to satisfy the ASRProvider interface.
        return


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
_provider_instance: ImportProvider | None = None


def get_provider() -> ImportProvider:
    global _provider_instance
    if _provider_instance is None:
        _provider_instance = ImportProvider()
    return _provider_instance
