fn main() {
    // Register our app command with the ACL so the capability can grant it
    // (autogenerates the `allow-show-native-toast` permission).
    tauri_build::try_build(
        tauri_build::Attributes::new().app_manifest(
            tauri_build::AppManifest::new().commands(&[
                "show_native_toast",
                "snooze_alert",
                "show_main",
                "hide_widget",
                "overlay_state",
                "get_desktop_settings",
                "set_desktop_settings",
                "list_monitors",
                "list_audio_devices",
                "armed_state",
                "capture_controller_button",
            ]),
        ),
    )
    .expect("failed to run tauri-build");
}
