# Wireless debugging

Use current [official Platform Tools](https://developer.android.com/tools/releases/platform-tools) and [ADB wireless debugging instructions](https://developer.android.com/tools/adb#connect-to-a-device-over-wi-fi). Android 11+ supports paired Wi-Fi debugging. The computer and phone need network reachability; guest Wi-Fi and client isolation can prevent it.

1. Check `command -v adb` and `adb version`. Install official Platform Tools in a user-owned SDK directory if missing; preserve existing SDK installations.
2. Have the user enable Developer options → Wireless debugging. If pairing is needed, they open **Pair device with pairing code**. Run `adb pair PHONE_IP:PAIRING_PORT` interactively and enter the short-lived code. Do not commit the code or put it in reusable scripts. Do not repeatedly try expired codes.
3. Check `adb mdns services` for `_adb-tls-connect._tcp`, or read **IP address & Port** on the main Wireless debugging screen. Use `adb connect PHONE_IP:CONNECTION_PORT`. The port in the pairing dialog is not the connection port; do not assume 5555.
4. Verify `adb devices -l`, then use the explicit serial shown there for every device command. Read `getprop ro.product.model` and `getprop ro.build.version.release` if needed to confirm the target.

For a dropped session, recheck discovery, the current connection port, and device state. Do not revoke all debugging keys or restart a shared ADB server as the first recovery step. Re-pair only if the pairing has actually been revoked. To end access, the user can disable Wireless debugging or forget the paired computer in its settings; `adb disconnect SERIAL` only ends the current transport.

Launch a known app with its resolved launcher activity:

```sh
adb -s "$PHONE_SERIAL" shell cmd package resolve-activity --brief com.example.app
adb -s "$PHONE_SERIAL" shell am start -n com.example.app/.MainActivity
```

Use the actual resolution result, not the illustrative component above. `input keyevent KEYCODE_WAKEUP` wakes the display but does not unlock it. If the user must unlock, ask and continue independent work while waiting.

UIAutomator dumps can fail with an idle timeout or null root without a useful exit status. Generate a unique remote filename each time, parse the returned XML, require a nonempty hierarchy, and remove only that generated file. The helper handles those steps. Consult screenshots for custom-drawn views and maps. Never treat an old XML file as a current screen.

## Keeping an authorized session awake

If the user explicitly asks to keep the unlocked phone awake during automation, a temporary extension of `settings get/put system screen_off_timeout` can avoid repeated idle locks. Record the original value in a private journal before changing it, verify the temporary value, and restore the original when finished. Before restoring, check that the current value still matches your temporary value so you do not overwrite a later user change. Do not alter the PIN, biometrics, or keyguard security. This is a session timeout adjustment, not an Android rule tied to ADB connectivity; if transport is lost before cleanup, report the pending restoration and restore when reachable.

Heads-up notifications can cover app controls even when the foreground package still reports the app. Inspect the latest UI/screenshot and wait for the overlay to clear before tapping an obscured control. A foreground-package guard alone does not prove the target item is unchanged; confirm the task/item identity before saving, especially if the user is also using the phone.
