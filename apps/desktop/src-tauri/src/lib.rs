use std::{
    io::Write,
    net::{IpAddr, Ipv4Addr, SocketAddr, TcpStream},
    path::PathBuf,
    process::{Child, Command, Stdio},
    sync::Mutex,
    thread,
    time::{Duration, Instant},
};
use tauri::{
    menu::{Menu, MenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    AppHandle, Emitter, LogicalSize, Manager, WebviewWindow,
};
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};
use uuid::Uuid;

const COMPACT_SIZE: (f64, f64) = (404.0, 164.0);
const EXPANDED_SIZE: (f64, f64) = (640.0, 720.0);
const SETTINGS_SIZE: (f64, f64) = (680.0, 720.0);

struct CoreSession {
    credential: String,
    child: Mutex<Option<Child>>,
}

impl Drop for CoreSession {
    fn drop(&mut self) {
        if let Ok(child) = self.child.get_mut() {
            if let Some(process) = child.as_mut() {
                request_core_shutdown(&self.credential);
                let deadline = Instant::now() + Duration::from_secs(3);
                while Instant::now() < deadline {
                    if process.try_wait().ok().flatten().is_some() {
                        return;
                    }
                    thread::sleep(Duration::from_millis(50));
                }
                terminate_core_process_tree(process);
            }
        }
    }
}

fn request_core_shutdown(credential: &str) {
    let address = SocketAddr::new(IpAddr::V4(Ipv4Addr::LOCALHOST), 8765);
    if let Ok(mut stream) = TcpStream::connect_timeout(&address, Duration::from_millis(250)) {
        let _ = stream.set_write_timeout(Some(Duration::from_millis(250)));
        let request = format!(
            "POST /shutdown HTTP/1.1\r\nHost: 127.0.0.1:8765\r\nAuthorization: Bearer {credential}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
        );
        let _ = stream.write_all(request.as_bytes());
        let _ = stream.flush();
    }
}

#[cfg(windows)]
fn terminate_core_process_tree(process: &mut Child) {
    use std::os::windows::process::CommandExt;

    let taskkill = std::env::var_os("SystemRoot")
        .map(PathBuf::from)
        .map(|root| root.join("System32").join("taskkill.exe"));
    if let Some(executable) = taskkill.filter(|path| path.is_file()) {
        let _ = Command::new(executable)
            .args(["/PID", &process.id().to_string(), "/T", "/F"])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .creation_flags(0x0800_0000)
            .status();
    }
    let _ = process.kill();
    let _ = process.wait();
}

#[cfg(not(windows))]
fn terminate_core_process_tree(process: &mut Child) {
    let _ = process.kill();
    let _ = process.wait();
}

#[tauri::command]
fn get_core_session_credential(session: tauri::State<'_, CoreSession>) -> String {
    session.credential.clone()
}

fn generate_core_credential() -> String {
    format!("{}{}", Uuid::new_v4().simple(), Uuid::new_v4().simple())
}

fn core_executable() -> Option<PathBuf> {
    let packaged = std::env::current_exe()
        .ok()?
        .parent()?
        .join("core")
        .join("moj-asystent-core.exe");
    if packaged.is_file() {
        return Some(packaged);
    }
    let development = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("..")
        .join("..")
        .join("services")
        .join("core")
        .join(".venv")
        .join("Scripts")
        .join("moj-asystent-core.exe");
    development.is_file().then_some(development)
}

fn start_core(credential: &str) -> Option<Child> {
    core_executable().and_then(|executable| {
        let mut command = Command::new(executable);
        command
            .env("MOJ_ASYSTENT_SESSION_CREDENTIAL", credential)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null());
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x0800_0000);
        }
        command.spawn().ok()
    })
}

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

    window
        .set_resizable(resizable)
        .map_err(|error| error.to_string())?;
    window
        .set_size(LogicalSize::new(width, height))
        .map_err(|error| error.to_string())?;
    Ok(())
}

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .invoke_handler(tauri::generate_handler![
            hide_overlay,
            set_overlay_mode,
            get_core_session_credential
        ])
        .setup(|app| {
            let credential = generate_core_credential();
            let child = start_core(&credential);
            app.manage(CoreSession {
                credential,
                child: Mutex::new(child),
            });
            let toggle =
                MenuItem::with_id(app, "toggle", "Otwórz / ukryj nakładkę", true, None::<&str>)?;
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
                    if let TrayIconEvent::Click {
                        button: MouseButton::Left,
                        button_state: MouseButtonState::Up,
                        ..
                    } = event
                    {
                        toggle_window(tray.app_handle());
                    }
                })
                .build(app)?;

            let shortcut = Shortcut::new(Some(Modifiers::CONTROL | Modifiers::SHIFT), Code::Space);
            app.global_shortcut()
                .on_shortcut(shortcut, |app, _, event| {
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

#[cfg(test)]
mod tests {
    use super::generate_core_credential;

    #[test]
    fn per_launch_credentials_are_unique_and_url_safe() {
        let first = generate_core_credential();
        let second = generate_core_credential();
        assert_ne!(first, second);
        assert_eq!(first.len(), 64);
        assert!(first.bytes().all(|byte| byte.is_ascii_hexdigit()));
    }
}
