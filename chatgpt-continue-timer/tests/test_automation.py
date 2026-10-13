import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from automation import (
    AutomationError, Target, blocked_reason, check_target, composer, discover, is_chatgpt_url,
    read_text, send_to_window, trusted_window, worker,
)
from pywinauto.uia_defines import NoPatternInterfaceError


class Control:
    def __init__(self, name="Ask anything", auto_id="prompt-textarea", text="",
                 visible=True, enabled=True, runtime_id=(1, 2), class_name=""):
        self.element_info = SimpleNamespace(
            name=name, automation_id=auto_id, runtime_id=runtime_id, class_name=class_name,
        )
        self.text = text
        self.visible = visible
        self.enabled = enabled
        self.focused = False
        self.child_controls = []
        self.parent_window = None

    def is_visible(self):
        return self.visible

    def is_enabled(self):
        return self.enabled

    def get_value(self):
        return self.text

    @property
    def iface_value(self):
        return SimpleNamespace(CurrentValue=self.text)

    def set_focus(self):
        self.focused = True

    def has_keyboard_focus(self):
        return self.focused

    def parent(self):
        return self.parent_window

    def children(self):
        return self.child_controls


class Window:
    handle = 123

    def __init__(self, fields=None, buttons=None, documents=None, texts=None):
        self.fields = fields if fields is not None else [Control()]
        self.buttons = buttons or []
        self.documents = documents or []
        self.texts = texts or []
        for field in self.fields:
            field.parent_window = self

    def process_id(self):
        return 99

    def window_text(self):
        return "Test conversation"

    def descendants(self, control_type):
        return {"Edit": self.fields, "Button": self.buttons,
                "Document": self.documents, "Text": self.texts}[control_type]

    def set_focus(self):
        pass


class AutomationTests(unittest.TestCase):
    def setUp(self):
        self.window = Window()
        self.field = self.window.fields[0]
        self.target = Target(123, 99, "Test conversation", (1, 2))

    def test_exact_url_matching(self):
        for url in ("https://chatgpt.com/c/123", "https://chat.openai.com/"):
            self.assertTrue(is_chatgpt_url(url))
        for url in ("http://chatgpt.com", "https://chatgpt.com.evil.example/",
                    "https://example.com/chatgpt.com", "file:///chatgpt.com",
                    "https://chatgpt.com@evil.example/", ""):
            self.assertFalse(is_chatgpt_url(url))

    def test_composer_id_beats_name(self):
        self.window.fields.append(Control(auto_id="", name="Ask anything"))
        self.assertIs(composer(self.window), self.field)

    def test_named_composer_fallback(self):
        self.field.element_info.automation_id = ""
        self.assertIs(composer(self.window), self.field)

    def test_reject_missing_hidden_disabled_or_ambiguous_composer(self):
        for fields in ([], [Control(visible=False)], [Control(enabled=False)],
                       [Control(name="Address", auto_id="")], [Control(), Control()]):
            with self.subTest(fields=fields), self.assertRaises(AutomationError):
                composer(Window(fields))

    @patch("psutil.Process")
    def test_trust_requires_browser_url_or_native_process(self, process):
        process.return_value.name.return_value = "msedge.exe"
        self.assertFalse(trusted_window(self.window))
        self.window.documents = [Control(text="https://chatgpt.com/c/123")]
        self.assertTrue(trusted_window(self.window))
        self.window.documents[0].visible = False
        self.assertFalse(trusted_window(self.window))
        process.return_value.name.return_value = "notepad.exe"
        self.assertFalse(trusted_window(self.window))
        process.return_value.name.return_value = "ChatGPT.exe"
        self.assertTrue(trusted_window(self.window))

    @patch("psutil.Process")
    def test_document_legacy_url(self, process):
        process.return_value.name.return_value = "firefox.exe"
        class LegacyDocument(Control):
            @property
            def iface_value(self):
                raise NoPatternInterfaceError()

        doc = LegacyDocument()
        doc.legacy_properties = lambda: {"Value": "https://chatgpt.com/"}
        self.window.documents = [doc]
        self.assertTrue(trusted_window(self.window))

    def test_text_pattern_fallback(self):
        field = Mock()
        field.element_info = SimpleNamespace(class_name="", name="")
        field.get_value.side_effect = NoPatternInterfaceError()
        field.iface_text.DocumentRange.GetText.return_value = "continue\r\n"
        self.assertEqual(read_text(field), "continue")

    def test_codex_composer_is_recognized(self):
        field = Control(name="Work with Codex", auto_id="")
        self.assertIs(composer(Window([field])), field)

    def test_prosemirror_generated_placeholder_is_empty(self):
        field = Control(name="Work with Codex", text="Work with Codex\n", class_name="ProseMirror")
        field.child_controls = [
            Control(name="Work with Codex"),
            Control(name="\n", class_name="ProseMirror-trailingBreak"),
        ]
        self.assertEqual(read_text(field), "")
        field.element_info.class_name = "ProseMirror ProseMirror-focused"
        self.assertEqual(read_text(field), "")
        field.text = "continue"
        self.assertEqual(read_text(field), "continue")

    def test_real_draft_equal_to_label_is_not_assumed_empty(self):
        field = Control(name="Work with Codex", text="Work with Codex", class_name="ProseMirror")
        self.assertEqual(read_text(field), "Work with Codex")

    @patch("automation.trusted_window", return_value=True)
    @patch("pywinauto.keyboard.send_keys")
    def test_credit_block_is_explicit_noop(self, keys, trust):
        self.window.buttons = [Control(name="Send", enabled=False)]
        self.window.texts = [Control(name="You're out of Codex and Work usage")]
        result = send_to_window(self.window, self.target)
        self.assertEqual(result.status, "blocked")
        self.assertIn("Out of credits", result.message)
        keys.assert_not_called()

    def test_empty_disabled_send_is_not_a_credit_block(self):
        self.window.buttons = [Control(name="Send", enabled=False)]
        self.assertEqual(blocked_reason(self.field), "")

    def test_invisible_historical_credit_message_is_ignored(self):
        self.window.buttons = [Control(name="Send", enabled=False)]
        self.window.texts = [Control(name="You're out of Codex and Work usage", visible=False)]
        self.assertEqual(blocked_reason(self.field), "")

    @patch("automation.trusted_window", return_value=True)
    @patch("pywinauto.keyboard.send_keys")
    def test_disabled_composer_is_explicit_noop(self, keys, trust):
        self.field.enabled = False
        result = send_to_window(self.window, self.target)
        self.assertEqual(result.status, "blocked")
        self.assertIn("disabled", result.message)
        keys.assert_not_called()

    @patch("automation.trusted_window", return_value=True)
    @patch("pywinauto.Desktop")
    def test_discovery_keeps_credit_blocked_codex_window(self, desktop, trust):
        self.field.element_info.name = "Work with Codex"
        self.field.element_info.automation_id = ""
        self.window.buttons = [Control(name="Send", enabled=False)]
        self.window.texts = [Control(name="Your workspace is out of credits.")]
        desktop.return_value.windows.return_value = [self.window]
        result = discover()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].app_name, "Codex")
        self.assertIn("Out of credits", result[0].unavailable_reason)

    @patch("automation.trusted_window", return_value=True)
    def test_changed_composer_or_process_is_rejected(self, trust):
        self.field.element_info.runtime_id = (9,)
        with self.assertRaises(AutomationError):
            check_target(self.window, self.target)
        self.window.process_id = lambda: 100
        with self.assertRaises(AutomationError):
            check_target(self.window, self.target)

    @patch("automation.trusted_window", return_value=True)
    def test_navigation_with_reused_composer_is_rejected(self, trust):
        self.window.documents = [Control(text="https://chatgpt.com/c/another")]
        with self.assertRaisesRegex(AutomationError, "conversation changed"):
            check_target(self.window, self.target)

    @patch("automation.trusted_window", return_value=True)
    @patch("pywinauto.keyboard.send_keys")
    def test_success_types_then_enters_once(self, keys, trust):
        def type_keys(text, **kwargs):
            self.field.text = "" if text == "{ENTER}" else text
        keys.side_effect = type_keys
        send_to_window(self.window, self.target)
        self.assertEqual([call.args[0] for call in keys.call_args_list], ["continue", "{ENTER}"])

    @patch("automation.trusted_window", return_value=True)
    @patch("pywinauto.keyboard.send_keys")
    def test_draft_is_not_overwritten(self, keys, trust):
        self.field.text = "my unsent message"
        with self.assertRaisesRegex(AutomationError, "draft"):
            send_to_window(self.window, self.target)
        keys.assert_not_called()
        self.assertEqual(self.field.text, "my unsent message")

    @patch("automation.trusted_window", return_value=True)
    @patch("pywinauto.keyboard.send_keys")
    def test_generation_is_not_interrupted(self, keys, trust):
        self.window.buttons = [Control(name="Stop generating")]
        with self.assertRaisesRegex(AutomationError, "generating"):
            send_to_window(self.window, self.target)
        keys.assert_not_called()

    @patch("automation.trusted_window", return_value=True)
    @patch("pywinauto.keyboard.send_keys")
    def test_focus_failure_does_not_type(self, keys, trust):
        self.field.set_focus = lambda: None
        with self.assertRaisesRegex(AutomationError, "focus"):
            send_to_window(self.window, self.target)
        keys.assert_not_called()

    @patch("automation.trusted_window", return_value=True)
    @patch("pywinauto.keyboard.send_keys")
    def test_typing_failure_does_not_press_enter(self, keys, trust):
        with self.assertRaisesRegex(AutomationError, "verify"):
            send_to_window(self.window, self.target)
        self.assertEqual(keys.call_count, 1)

    @patch("automation.trusted_window", return_value=True)
    @patch("pywinauto.keyboard.send_keys")
    def test_focus_change_does_not_press_enter(self, keys, trust):
        def type_keys(text, **kwargs):
            self.field.text = text
            self.field.focused = False
        keys.side_effect = type_keys
        with self.assertRaisesRegex(AutomationError, "Focus changed"):
            send_to_window(self.window, self.target)
        self.assertEqual(keys.call_count, 1)

    @patch("automation.time.monotonic", side_effect=[0, 0, 0, 10])
    @patch("automation.trusted_window", return_value=True)
    @patch("pywinauto.keyboard.send_keys")
    def test_uncertain_submission_never_retries(self, keys, trust, clock):
        def type_keys(text, **kwargs):
            if text != "{ENTER}":
                self.field.text = text
        keys.side_effect = type_keys
        with self.assertRaisesRegex(AutomationError, "will not retry"):
            send_to_window(self.window, self.target)
        self.assertEqual(keys.call_count, 2)

    @patch("automation.trusted_window", return_value=True)
    @patch("pywinauto.Desktop")
    def test_discovery(self, desktop, trust):
        desktop.return_value.windows.return_value = [self.window]
        self.assertEqual(discover(), [Target(123, 99, "Test conversation", (1, 2))])

    @patch("automation.discover", side_effect=AutomationError("test failure"))
    def test_worker_surfaces_error(self, discover_mock):
        queue = Mock()
        worker("discover", None, queue)
        queue.put.assert_called_once_with(("error", "AutomationError: test failure"))


if __name__ == "__main__":
    unittest.main()
