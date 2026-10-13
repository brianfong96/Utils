"""Windows accessibility targeting. Never use coordinates or overwrite drafts."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
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


COMPOSER_NAMES = {"ask anything", "message chatgpt", "send a message", "message"}
SEND_NAMES = {"send", "send message", "send prompt"}
STOP_NAMES = {"stop", "stop generating", "stop streaming"}
BROWSER_NAMES = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe"}


def is_chatgpt_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme == "https" and parsed.hostname in {"chatgpt.com", "chat.openai.com"}


def read_text(control) -> str:
    from pywinauto.uia_defines import NoPatternInterfaceError

    try:
        return control.get_value()
    except NoPatternInterfaceError:
        try:
            return control.iface_text.DocumentRange.GetText(-1).rstrip("\r\n")
        except NoPatternInterfaceError as error:
            raise AutomationError(
                "The message box does not expose its contents. Nothing was sent."
            ) from error


def composer(window):
    candidates = []
    for control in window.descendants(control_type="Edit"):
        if not control.is_visible() or not control.is_enabled():
            continue
        info = control.element_info
        if info.automation_id == "prompt-textarea":
            candidates.append((2, control))
        elif info.name.strip().casefold() in COMPOSER_NAMES:
            candidates.append((1, control))
    if not candidates:
        raise AutomationError(
            "No accessible ChatGPT message box found. Keep the conversation tab visible "
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
                url = document.iface_legacy_iaccessible.CurrentValue
            except NoPatternInterfaceError:
                continue
        if url:
            return url
    return ""


def trusted_window(window) -> bool:
    import psutil

    name = psutil.Process(window.process_id()).name().casefold()
    if name == "chatgpt.exe":
        return True
    return name in BROWSER_NAMES and is_chatgpt_url(document_url(window))


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
            field = composer(window)
            targets.append(Target(
                window.handle, window.process_id(), window.window_text(),
                tuple(field.element_info.runtime_id), document_url(window),
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
    field = composer(window)
    if tuple(field.element_info.runtime_id) != target.composer_id:
        raise AutomationError(
            "The selected conversation changed or reloaded. Refresh the window list and restart the timer."
        )
    return field


def send_to_window(window, target: Target) -> None:
    from pywinauto import keyboard

    field = check_target(window, target)
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
    if not field.has_keyboard_focus():
        raise AutomationError("Focus changed before sending. Enter was not pressed.")
    keyboard.send_keys("{ENTER}")
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if not read_text(field).strip():
            return
        time.sleep(0.1)
    raise AutomationError(
        "Enter was pressed, but the message box did not clear. Check ChatGPT; "
        "the app will not retry or send a duplicate."
    )


def send(target: Target) -> None:
    from pywinauto import Desktop

    window = Desktop(backend="uia").window(handle=target.handle).wrapper_object()
    send_to_window(window, target)


def worker(operation: str, target: Target | None, queue) -> None:
    try:
        if operation == "discover":
            result = discover()
        elif operation == "send" and target is not None:
            send(target)
            result = None
        else:
            raise AutomationError("Invalid automation operation.")
        queue.put(("ok", result))
    except Exception as error:
        # Boundary of the isolated worker: failures must reach the UI.
        queue.put(("error", f"{type(error).__name__}: {error}"))
