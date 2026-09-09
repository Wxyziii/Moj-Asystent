use tauri::{
    menu::{Menu, MenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    AppHandle, Emitter, LogicalSize, Manager, WebviewWindow,
};
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};

const COMPACT_SIZE: (f64, f64) = (404.0, 164.0);
const EXPANDED_SIZE: (f64, f64) = (640.0, 720.0);
const SETTINGS_SIZE: (f64, f64) = (680.0, 720.0);

fn show_window(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.set_focus();
    }
}

fn toggle_window(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        if window.is_visible().unwrap_or(false) {
            let _ = window.hide();
        } else {
            show_window(app);
        }
    }
}

#[tauri::command]
fn hide_overlay(window: WebviewWindow) {
    let _ = window.hide();
}

#[tauri::command]
fn set_overlay_mode(window: WebviewWindow, mode: String) -> Result<(), String> {
    let (width, height, resizable) = match mode.as_str() {
        "compact" => (COMPACT_SIZE.0, COMPACT_SIZE.1, false),
        "expanded" => (EXPANDED_SIZE.0, EXPANDED_SIZE.1, true),
        "settings" => (SETTINGS_SIZE.0, SETTINGS_SIZE.1, true),
        _ => return Err("Nieznany tryb nakładki".to_string()),
    };

    window.set_resizable(resizable).map_err(|error| error.to_string())?;
    window
        .set_size(LogicalSize::new(width, height))
        .map_err(|error| error.to_string())?;
    Ok(())
}

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .invoke_handler(tauri::generate_handler![hide_overlay, set_overlay_mode])
        .setup(|app| {
            let toggle = MenuItem::with_id(app, "toggle", "Otwórz / ukryj nakładkę", true, None::<&str>)?;
            let chat = MenuItem::with_id(app, "chat", "Otwórz czat", true, None::<&str>)?;
            let settings = MenuItem::with_id(app, "settings", "Ustawienia", true, None::<&str>)?;
            let separator = tauri::menu::PredefinedMenuItem::separator(app)?;
            let quit = MenuItem::with_id(app, "quit", "Zakończ", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&toggle, &chat, &settings, &separator, &quit])?;

            TrayIconBuilder::with_id("assistant-tray")
                .menu(&menu)
                .tooltip("Mój Asystent")
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "toggle" => toggle_window(app),
                    "chat" => {
                        show_window(app);
                        let _ = app.emit("overlay-command", "open-chat");
                    }
                    "settings" => {
                        show_window(app);
                        let _ = app.emit("overlay-command", "open-settings");
                    }
                    "quit" => app.exit(0),
                    _ => {}
                })
                .on_tray_icon_event(|tray, event| {
                    if let TrayIconEvent::Click { button: MouseButton::Left, button_state: MouseButtonState::Up, .. } = event {
                        toggle_window(&tray.app_handle());
                    }
                })
                .build(app)?;

            let shortcut = Shortcut::new(Some(Modifiers::CONTROL | Modifiers::SHIFT), Code::Space);
            app.global_shortcut().on_shortcut(shortcut, |app, _, event| {
                if event.state() == ShortcutState::Pressed {
                    toggle_window(app);
                }
            })?;
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .run(tauri::generate_context!())
        .expect("nie udało się uruchomić aplikacji Mój Asystent");
}
