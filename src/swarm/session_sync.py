from typing import Any, Dict, List, Optional


def build_chat_message(
    role: str, content: str, reasoning: Optional[str] = None
) -> Dict[str, Any]:
    message = {"role": role, "content": content}
    if reasoning:
        message["reasoning"] = reasoning
    return message


def append_chat_message(
    chat_history: List[Dict[str, Any]],
    role: str,
    content: str,
    reasoning: Optional[str] = None,
) -> List[Dict[str, Any]]:
    return [*chat_history, build_chat_message(role, content, reasoning)]


def build_session_update(
    scope_text: str, chat_history: List[Dict[str, Any]]
) -> Dict[str, Any]:
    return {"scope_text": scope_text, "chat_history": chat_history}
