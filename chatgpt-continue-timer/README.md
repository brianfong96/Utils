# ChatGPT Continue Timer

A small Windows Python app that sends `continue` once to an already-open
ChatGPT or Codex conversation after a delay. Default: three hours. No browser profile,
API key, debugging port, coordinates, or screen-resolution configuration.

## Double-click to run

Install **Python 3.11 or later** from python.org (include the Python launcher),
then double-click **Start ChatGPT Timer.cmd**. On first launch it installs the
dependencies into a local `.venv`; subsequent launches open the app directly.
First-time setup needs internet access.

1. Open the desired ChatGPT or Codex conversation and leave it selected.
2. The app finds windows on launch. Select the desired window; click **Find window** to refresh.
3. Set hours, minutes, and seconds and click **Start timer**.

The app brings the selected window forward, finds the message box through
Windows UI Automation, types `continue`, and presses Enter. It checks that the
box clears, not that ChatGPT successfully completes a response. **Cancel timer**
stops a pending countdown. It does not repeat or automatically retry.

Keep the timer app open and the PC awake, unlocked, and logged in. It cannot
wake the computer or work on the lock screen. A timer that expires during sleep
runs on resume, not while asleep. Closing the app loses the timer.

## Compatibility and safeguards

Windows only. It supports browsers exposing ChatGPT's URL and message box to
accessibility (Chrome, Edge, Firefox, Brave, Opera, Vivaldi), plus an accessible
ChatGPT/Codex desktop app. The desktop app's **Work with Codex** composer is
recognized, including its generated empty-editor placeholder. Browser support is capability-based, not guaranteed for
every version or language. Only visible/selected tabs are discoverable; tabs
hidden behind other tabs are not searched or switched automatically.

If discovery finds nothing, select the conversation tab, restore the window,
and try again. Chromium browsers may need accessibility enabled in their
accessibility settings (`chrome://accessibility` or `edge://accessibility`).
English accessible message-box names or the `prompt-textarea` ID are supported.

An out-of-credits Codex window still appears in the picker with an explanation.
You can schedule a future check; the app rechecks when the timer expires.
If the composer remains disabled, or its Send button is disabled alongside a
visible credit-limit banner, the timer finishes with an explicit **nothing
typed or sent** result. It cannot restore credits or bypass a usage limit.

It refuses non-ChatGPT browser URLs, existing drafts, ambiguous message boxes,
changed/reloaded conversations, failed focus, and visible ongoing generation.
Do not switch conversations, reload, or use the keyboard during submission.
Window identity is retained from discovery; refresh the list after a reload.
Accessibility and focus can change externally, so inspect the result before
manually retrying any failed/timed-out submission. This is desktop automation,
not a background ChatGPT API client.

## Standalone double-click EXE

Double-click **Build Windows App.cmd**. The result is
`dist\ChatGPTContinueTimer.exe`. Copy this single file anywhere on Windows;
Python and the source folder are not required to run it. Build on Windows.

## Development

```powershell
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python app.py --smoke-test
python app.py
```

Tests use fake accessibility controls and never send messages to your account.
An additional opt-in integration test opens an isolated Chrome (or Edge) profile with a
local HTML fixture, exercises real accessibility focus/typing/Enter, and closes
only that fixture:

```powershell
$env:CONTINUE_TIMER_NATIVE_TEST = "1"
python -m unittest discover -s tests -v
```

On a restricted build host where Chromium's sandbox cannot start a renderer,
set `CONTINUE_TIMER_TEST_NO_SANDBOX=1` for this **local fixture test only**.
Do not use that browser instance for real browsing. The timer never starts
your browser or changes its security flags.
