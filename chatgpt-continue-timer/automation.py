"""Windows accessibility targeting. Never use coordinates or overwrite drafts."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse


class AutomationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Target:
    handle: int
    process_id: int
    title: str
    composer_id: tuple[int, ...]
    location: str = ""
    app_name: str = "ChatGPT"
    unavailable_reason: str = ""


@dataclass(frozen=True)
class SendResult:
    status: Literal["submitted", "blocked"]
    message: str


COMPOSER_NAMES = {"ask anything", "message chatgpt", "send a message", "message", "work with codex"}
SEND_NAMES = {"send", "send message", "send prompt"}
STOP_NAMES = {"stop", "stop generating", "stop streaming"}
BROWSER_NAMES = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe"}


def is_chatgpt_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme == "https" and parsed.hostname in {"chatgpt.com", "chat.openai.com"}


def read_text(control) -> str:
    from pywinauto.uia_defines import NoPatternInterfaceError

    try:
        text = control.get_value()
    except NoPatternInterfaceError:
        try:
            text = control.iface_text.DocumentRange.GetText(-1).rstrip("\r\n")
        except NoPatternInterfaceError as error:
            raise AutomationError(
                "The message box does not expose its contents. Nothing was sent."
            ) from error
    # Chromium includes ProseMirror's generated empty-editor placeholder in UIA values.
    if ("ProseMirror" in control.element_info.class_name.split()
            and text.strip() == control.element_info.name.strip()):
        children = control.children()
        if (len(children) == 2
                and children[0].element_info.name == control.element_info.name
                and "ProseMirror-trailingBreak" in children[1].element_info.class_name.split()
                and not children[1].element_info.name.strip()):
            return ""
    return text


def composer(window, include_disabled: bool = False):
    candidates = []
    for control in window.descendants(control_type="Edit"):
        if not control.is_visible() or (not include_disabled and not control.is_enabled()):
            continue
        info = control.element_info
        if info.automation_id == "prompt-textarea":
            candidates.append((2, control))
        elif info.name.strip().casefold() in COMPOSER_NAMES:
            candidates.append((1, control))
    if not candidates:
        raise AutomationError(
            "No accessible ChatGPT/Codex message box found. Keep the conversation tab visible "
            "and enable browser accessibility if needed."
        )
    best = max(rank for rank, _ in candidates)
    matches = [control for rank, control in candidates if rank == best]
    if len(matches) != 1:
        raise AutomationError("More than one message box matches. Nothing was sent.")
    return matches[0]


def document_url(window) -> str:
    from pywinauto.uia_defines import NoPatternInterfaceError

    for document in window.descendants(control_type="Document"):
        if not document.is_visible():
            continue
        try:
            url = document.iface_value.CurrentValue
        except NoPatternInterfaceError:
            try:
                url = document.legacy_properties()["Value"]
            except NoPatternInterfaceError:
                continue
        if url:
            return url
    return ""


def trusted_window(window) -> bool:
    import psutil

    name = psutil.Process(window.process_id()).name().casefold()
    if name in {"chatgpt.exe", "codex.exe"}:
        return True
    return name in BROWSER_NAMES and is_chatgpt_url(document_url(window))


def blocked_reason(field) -> str:
    if not field.is_enabled():
        return "The message box is disabled."
    group = field.parent()
    send_buttons = [
        button for button in group.descendants(control_type="Button")
        if button.is_visible() and button.element_info.name.strip().casefold() in SEND_NAMES
    ]
    if not send_buttons or any(button.is_enabled() for button in send_buttons):
        return ""
    for control in group.descendants(control_type="Text"):
        text = control.element_info.name.strip().casefold().replace("\u2019", "'")
        if control.is_visible() and (
                text.startswith(("you're out of codex and work usage", "your workspace is out of credits"))
                or text == "you've hit your usage limit"):
            return "Out of credits or usage allowance."
    return ""


def discover() -> list[Target]:
    import psutil
    from pywinauto import Desktop

    targets = []
    for window in Desktop(backend="uia").windows():
        if window.process_id() == os.getpid():
            continue
        try:
            if not trusted_window(window):
                continue
            field = composer(window, include_disabled=True)
            targets.append(Target(
                window.handle, window.process_id(), window.window_text(),
                tuple(field.element_info.runtime_id), document_url(window),
                "Codex" if field.element_info.name.strip().casefold() == "work with codex" else "ChatGPT",
                blocked_reason(field),
            ))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        except AutomationError:
            # An inaccessible conversation is not a selectable target.
            continue
    return targets


def check_target(window, target: Target):
    if window.process_id() != target.process_id or not trusted_window(window):
        raise AutomationError("The selected ChatGPT window closed or changed. Nothing was sent.")
    if window.window_text() != target.title or document_url(window) != target.location:
        raise AutomationError("The selected conversation changed. Nothing was sent.")
    field = composer(window, include_disabled=True)
    if tuple(field.element_info.runtime_id) != target.composer_id:
        raise AutomationError(
            "The selected conversation changed or reloaded. Refresh the window list and restart the timer."
        )
    return field


def send_to_window(window, target: Target) -> SendResult:
    from pywinauto import keyboard

    field = check_target(window, target)
    if not field.is_enabled():
        return SendResult("blocked", "The message box is disabled. Nothing typed or sent.")
    if read_text(field).strip():
        raise AutomationError("The message box contains a draft. It was left untouched.")
    for button in window.descendants(control_type="Button"):
        if button.is_visible() and button.element_info.name.strip().casefold() in STOP_NAMES:
            raise AutomationError("ChatGPT is still generating a response. Nothing was sent.")
    window.set_focus()
    field.set_focus()
    focus_deadline = time.monotonic() + 2
    while not field.has_keyboard_focus() and time.monotonic() < focus_deadline:
        time.sleep(0.05)
    if not field.has_keyboard_focus():
        raise AutomationError("Could not focus the ChatGPT message box. Nothing was typed.")
    if read_text(field).strip():
        raise AutomationError("A draft appeared in the message box. It was left untouched.")
    keyboard.send_keys("continue", pause=0.05)
    text_deadline = time.monotonic() + 2
    while read_text(field).strip() != "continue" and time.monotonic() < text_deadline:
        if not field.has_keyboard_focus():
            raise AutomationError("Focus changed while typing. Enter was not pressed.")
        time.sleep(0.05)
    if read_text(field).strip() != "continue":
        raise AutomationError("Could not verify the typed message. Enter was not pressed.")
    check_target(window, target)
    if not field.is_enabled():
        return SendResult("blocked", 'Typed "continue", but the message box became disabled '
                          'before Enter. The draft was left in the box.')
    if not field.has_keyboard_focus():
        raise AutomationError("Focus changed before sending. Enter was not pressed.")
    keyboard.send_keys("{ENTER}")
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if not read_text(field).strip():
            return SendResult("submitted", 'Submitted "continue". The message box cleared; timer finished.')
        reason = blocked_reason(field)
        if reason and read_text(field).strip() == "continue":
            return SendResult("blocked", 'Typed "continue" and pressed Enter. ' + reason
                              + ' The draft is still in the box; no retry will be made.')
        time.sleep(0.1)
    raise AutomationError(
        "Enter was pressed, but the message box did not clear. Check ChatGPT; "
        "the app will not retry or send a duplicate."
    )


def send(target: Target) -> SendResult:
    from pywinauto import Desktop

    window = Desktop(backend="uia").window(handle=target.handle).wrapper_object()
    return send_to_window(window, target)


def worker(operation: str, target: Target | None, queue) -> None:
    try:
        if operation == "discover":
            result = discover()
        elif operation == "send" and target is not None:
            result = send(target)
        else:
            raise AutomationError("Invalid automation operation.")
        queue.put(("ok", result))
    except Exception as error:
        # Boundary of the isolated worker: failures must reach the UI.
        queue.put(("error", f"{type(error).__name__}: {error}"))
