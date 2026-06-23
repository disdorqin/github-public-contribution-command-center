from prompt_toolkit.formatted_text.html import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.shortcuts import PromptSession

from minisweagent import global_config_dir

_history = FileHistory(global_config_dir / "interactive_history.txt")

# Lazy initialization to avoid console errors in non-interactive environments
_prompt_session = None
_multiline_prompt_session = None


def _get_prompt_session() -> PromptSession:
    global _prompt_session
    if _prompt_session is None:
        _prompt_session = PromptSession(history=_history)
    return _prompt_session


def _get_multiline_prompt_session() -> PromptSession:
    global _multiline_prompt_session
    if _multiline_prompt_session is None:
        _multiline_prompt_session = PromptSession(history=_history, multiline=True)
    return _multiline_prompt_session


def _multiline_prompt() -> str:
    return _get_multiline_prompt_session().prompt(
        "",
        bottom_toolbar=HTML(
            "Submit message: <b fg='yellow' bg='black'>Esc, then Enter</b> | "
            "Navigate history: <b fg='yellow' bg='black'>Arrow Up/Down</b> | "
            "Search history: <b fg='yellow' bg='black'>Ctrl+R</b>"
        ),
    )
