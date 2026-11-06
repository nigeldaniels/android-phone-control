# Android Phone Control

A Codex skill and small Python helper for operating Android apps through paired ADB, particularly mobile-only controls missing from service integrations.

## Install

Requires Python 3.10+ and current Android SDK Platform Tools (`adb`) on PATH. Pair the phone using [the setup guide](references/wireless-setup.md).

Clone this repository, then link it into your Codex skills directory:

```sh
git clone git@github.com:nigeldaniels/android-phone-control.git
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills"
ln -s "$PWD/android-phone-control" "${CODEX_HOME:-$HOME/.codex}/skills/android-phone-control"
```

If that skill path already exists, inspect it before replacing it. Start a new Codex session to discover the installed skill. Invoke `$android-phone-control` or ask for an Android phone UI action.

## Helper

```sh
python3 scripts/android_ui.py --help
adb devices -l
```

Supply an explicit device serial and app package. Inspect fresh UI state, perform one action, and verify the saved app state. Runtime artifacts stay in a private state directory outside the checkout. No device credentials, pairing data, or screenshots are included in this repository.

With `PHONE_SERIAL` set to the serial you selected from `adb devices -l`:

```sh
python3 scripts/android_ui.py --serial "$PHONE_SERIAL" snapshot --package com.example.app
python3 scripts/android_ui.py --serial "$PHONE_SERIAL" tap --package com.example.app --rid com.example.app:id/title
python3 scripts/android_ui.py --serial "$PHONE_SERIAL" type --package com.example.app --rid com.example.app:id/title --expected-text 'Old title' --value 'New title' --replace
```

Replace the example package and resource ID with values from the fresh snapshot. Typing requires the field to be focused already. A tap returns `dispatched-and-observed`; it does not assert that the app saved a task. Exit code 3 means input may have taken effect and must be reconciled before retrying. The helper performs no automatic retries of mutations.

## Scope and verification

The helper supports UI hierarchy capture, screenshots, guarded taps and swipes, and verified text entry. The skill covers paired Wi-Fi debugging, app navigation, and checking saved app state. Any.do notes describe reminder, location, priority, and grocery-list controls.

Saved settings and real-world notification delivery require separate verification. The Any.do location editor described in the notes does not expose a configurable radius; inspect the current app before promising a requested distance or another control.

Run helper tests with `python3 -m unittest discover -s tests -v`. See [SKILL.md](SKILL.md) for the operating workflow and [Any.do notes](references/anydo.md) for app-specific findings.
