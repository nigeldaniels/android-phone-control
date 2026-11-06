---
name: android-phone-control
description: Inspect and operate Android phone apps through a paired ADB connection, including mobile-only controls missing from connected service tools. Use for Android UI automation and wireless debugging setup, not ordinary service API work.
---

# Android phone control

Use connected service tools when they support the requested operation. Use the phone UI for controls they cannot expose. Read [wireless setup](references/wireless-setup.md) when connecting or recovering a connection, and [Any.do](references/anydo.md) for task reminders and priority.

## Observe, act, verify

1. Run `adb devices -l` and select the user's device explicitly. Every device command uses `adb -s SERIAL`; never select the first available device implicitly. Connection and pairing ports are different and can change.
2. Check the foreground app. The user unlocks their phone; do not request or enter a PIN/password. Preserve lock settings unless the user explicitly authorizes a change. For an authorized temporary screen-timeout extension, follow [wireless setup](references/wireless-setup.md) and restore the original value afterward. A missing expected package can also indicate a dialog, another app, or a UI dump failure: inspect before assuming the phone locked.
3. Use `scripts/android_ui.py --help`. Capture a fresh hierarchy, select the intended visible control by exact resource ID, text or description, and require its package. When text is duplicated, add another selector. Screenshots help with maps and controls absent from accessibility; inspect them before any coordinate-based action.
4. Execute one meaningful action, then inspect the resulting screen. The helper captures again before each mutation, rejects ambiguous/disabled/password fields, and writes a private action record. For typing, focus the field, observe its exact current text, and supply that as the expected value. The helper supports printable ASCII; use an app-supported mechanism or user input for other characters, not a speculative encoding workaround.
5. Save through the app's actual control. Reopen the edited item and verify the requested values. A successful tap or snapshot means input was delivered, not that the intended change was saved. Use a service readback too when the relevant field is exposed.

Keep existing authorization across navigation and saving. Do not add approval questions for routine steps already requested. Stop dependent actions when the app cannot express a required value; explain the limitation and seek a product choice before substituting another app, radius, date, or reminder type.

After a timeout or disconnected response during mutation, the outcome is unknown. Reconnect and inspect the app and private record before deciding whether another action is needed. If readback cannot determine whether Save succeeded, leave the outcome unresolved and do not repeat Save or recreate the item. The helper records evidence; it does not provide cross-run idempotency. Do not replay a sequence blindly. If UIAutomator omits a control, inspect a new screenshot and current bounds; a failed dump is not a usable old snapshot.

## Data and operational limits

Runtime screenshots, XML and records can contain personal information. The helper stores them under `~/.local/state/android-phone-control/` (or `XDG_STATE_HOME`) with private permissions. Keep them out of the repository. Do not publish device identifiers, pairing codes, ADB keys, account data, or screenshots with the reusable skill. Read only the app state relevant to the task; use the public app UI rather than extracting private app databases or credentials.

Do not root the phone, disable security controls, spoof location, or switch to legacy unauthenticated TCP debugging to work around a UI limit. Change app permissions only when necessary for the user's requested behavior and verify them. ADB automation runs while the computer can reach the phone; background reminders must be saved in an app that runs on the phone.

Report separately: connection tested, UI actions performed, saved settings verified, and real-world notification delivery tested. A saved geofence is not proof that a notification has fired at its boundary.
