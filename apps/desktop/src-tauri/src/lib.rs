use serde::{Deserialize, Serialize};
use std::{
    io::{Read, Write},
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
    AppHandle, Emitter, LogicalSize, Manager, PhysicalPosition, PhysicalSize, WebviewWindow,
};
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};
use uuid::Uuid;

const COMPACT_SIZE: (f64, f64) = (404.0, 164.0);
const EXPANDED_SIZE: (f64, f64) = (640.0, 720.0);
const SETTINGS_SIZE: (f64, f64) = (680.0, 720.0);

struct CoreSession {
    credential: String,
    action_credential: String,
    child: Mutex<Option<Child>>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ConfirmationDecisionInput {
    confirmation_id: String,
    operation_id: String,
    call_id: String,
    tool_name: String,
    arguments_digest: String,
    decision: String,
}

#[derive(Clone, Serialize)]
struct RegionSelectionGeometry {
    left: i32,
    top: i32,
    width: u32,
    height: u32,
    scale_factor: f64,
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

fn validate_confirmation_decision(decision: &ConfirmationDecisionInput) -> Result<(), String> {
    let token_valid = (32..=128).contains(&decision.confirmation_id.len())
        && decision
            .confirmation_id
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'));
    let mut tool_bytes = decision.tool_name.bytes();
    let tool_valid = (2..=64).contains(&decision.tool_name.len())
        && tool_bytes
            .next()
            .is_some_and(|byte| byte.is_ascii_lowercase())
        && tool_bytes
            .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'_');
    let digest_valid = decision.arguments_digest.len() == 64
        && decision
            .arguments_digest
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte));
    let ids_valid = Uuid::parse_str(&decision.operation_id).is_ok()
        && Uuid::parse_str(&decision.call_id).is_ok();
    let choice_valid = matches!(
        decision.decision.as_str(),
        "allow" | "always_allow" | "cancel"
    );

    if token_valid && tool_valid && digest_valid && ids_valid && choice_valid {
        Ok(())
    } else {
        Err("Nieprawidłowa odpowiedź na prośbę o zgodę".to_string())
    }
}

fn post_confirmation_decision(
    action_credential: &str,
    decision: &ConfirmationDecisionInput,
) -> Result<(), String> {
    validate_confirmation_decision(decision)?;
    let body = serde_json::json!({
        "protocol_version": "1.4",
        "confirmation_id": decision.confirmation_id,
        "operation_id": decision.operation_id,
        "call_id": decision.call_id,
        "tool_name": decision.tool_name,
        "arguments_digest": decision.arguments_digest,
        "decision": decision.decision,
    })
    .to_string();
    let address = SocketAddr::new(IpAddr::V4(Ipv4Addr::LOCALHOST), 8765);
    let mut stream = TcpStream::connect_timeout(&address, Duration::from_secs(1))
        .map_err(|_| "Rdzeń asystenta jest niedostępny".to_string())?;
    stream
        .set_read_timeout(Some(Duration::from_secs(2)))
        .map_err(|_| "Nie udało się ustawić limitu czasu".to_string())?;
    stream
        .set_write_timeout(Some(Duration::from_secs(2)))
        .map_err(|_| "Nie udało się ustawić limitu czasu".to_string())?;
    let request = format!(
        "POST /tool-confirmations/resolve HTTP/1.1\r\nHost: 127.0.0.1:8765\r\nAuthorization: Bearer {action_credential}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
        body.len()
    );
    stream
        .write_all(request.as_bytes())
        .map_err(|_| "Nie udało się wysłać decyzji".to_string())?;
    stream
        .flush()
        .map_err(|_| "Nie udało się wysłać decyzji".to_string())?;

    let mut response = String::new();
    stream
        .take(8192)
        .read_to_string(&mut response)
        .map_err(|_| "Nie udało się odczytać odpowiedzi rdzenia".to_string())?;
    match parse_http_status(response.lines().next().unwrap_or_default()) {
        Some(200) => Ok(()),
        Some(409) => Err("Ta prośba o zgodę wygasła albo została już rozpatrzona".to_string()),
        _ => Err("Rdzeń odrzucił decyzję o zgodzie".to_string()),
    }
}

fn parse_http_status(line: &str) -> Option<u16> {
    let mut fields = line.trim_end_matches('\r').splitn(3, ' ');
    if !matches!(fields.next(), Some("HTTP/1.0" | "HTTP/1.1")) {
        return None;
    }
    let code = fields.next()?;
    if code.len() != 3 || !code.bytes().all(|byte| byte.is_ascii_digit()) {
        return None;
    }
    code.parse().ok()
}

#[tauri::command]
async fn resolve_tool_confirmation(
    session: tauri::State<'_, CoreSession>,
    decision: ConfirmationDecisionInput,
) -> Result<(), String> {
    let action_credential = session.action_credential.clone();
    tauri::async_runtime::spawn_blocking(move || {
        post_confirmation_decision(&action_credential, &decision)
    })
    .await
    .map_err(|_| "Nie udało się przekazać decyzji".to_string())?
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

fn start_core(credential: &str, action_credential: &str) -> Option<Child> {
    core_executable().and_then(|executable| {
        let mut command = Command::new(executable);
        command
            .env("MOJ_ASYSTENT_SESSION_CREDENTIAL", credential)
            .env("MOJ_ASYSTENT_ACTION_CREDENTIAL", action_credential)
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

#[tauri::command]
fn begin_region_selection(app: AppHandle) -> Result<(), String> {
    let main = app
        .get_webview_window("main")
        .ok_or_else(|| "Brak okna nakładki".to_string())?;
    let monitor = main
        .current_monitor()
        .map_err(|error| error.to_string())?
        .or(main.primary_monitor().map_err(|error| error.to_string())?)
        .ok_or_else(|| "Nie znaleziono monitora".to_string())?;
    let selector = app
        .get_webview_window("region-selector")
        .ok_or_else(|| "Brak selektora regionu".to_string())?;
    main.hide().map_err(|error| error.to_string())?;
    selector
        .set_position(PhysicalPosition::new(
            monitor.position().x,
            monitor.position().y,
        ))
        .map_err(|error| error.to_string())?;
    selector
        .set_size(PhysicalSize::new(
            monitor.size().width,
            monitor.size().height,
        ))
        .map_err(|error| error.to_string())?;
    selector.show().map_err(|error| error.to_string())?;
    selector.set_focus().map_err(|error| error.to_string())?;
    Ok(())
}

#[tauri::command]
fn region_selection_geometry(window: WebviewWindow) -> Result<RegionSelectionGeometry, String> {
    if window.label() != "region-selector" {
        return Err("Geometria jest dostępna tylko dla selektora".to_string());
    }
    let monitor = window
        .current_monitor()
        .map_err(|error| error.to_string())?
        .ok_or_else(|| "Nie znaleziono monitora".to_string())?;
    Ok(RegionSelectionGeometry {
        left: monitor.position().x,
        top: monitor.position().y,
        width: monitor.size().width,
        height: monitor.size().height,
        scale_factor: monitor.scale_factor(),
    })
}

#[tauri::command]
fn hide_region_selector(window: WebviewWindow) -> Result<(), String> {
    if window.label() != "region-selector" {
        return Err("Nieprawidłowe okno selektora".to_string());
    }
    window.hide().map_err(|error| error.to_string())
}

#[tauri::command]
fn finish_region_selection(app: AppHandle, window: WebviewWindow) -> Result<(), String> {
    hide_region_selector(window)?;
    let main = app
        .get_webview_window("main")
        .ok_or_else(|| "Brak okna nakładki".to_string())?;
    main.set_resizable(true)
        .map_err(|error| error.to_string())?;
    main.set_size(LogicalSize::new(EXPANDED_SIZE.0, EXPANDED_SIZE.1))
        .map_err(|error| error.to_string())?;
    main.show().map_err(|error| error.to_string())?;
    main.set_focus().map_err(|error| error.to_string())
}

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .invoke_handler(tauri::generate_handler![
            hide_overlay,
            set_overlay_mode,
            get_core_session_credential,
            resolve_tool_confirmation,
            begin_region_selection,
            region_selection_geometry,
            hide_region_selector,
            finish_region_selection
        ])
        .setup(|app| {
            let credential = generate_core_credential();
            let action_credential = generate_core_credential();
            let child = start_core(&credential, &action_credential);
            app.manage(CoreSession {
                credential,
                action_credential,
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
    use super::{
        generate_core_credential, parse_http_status, validate_confirmation_decision,
        ConfirmationDecisionInput,
    };
    use uuid::Uuid;

    #[test]
    fn per_launch_credentials_are_unique_and_url_safe() {
        let first = generate_core_credential();
        let second = generate_core_credential();
        assert_ne!(first, second);
        assert_eq!(first.len(), 64);
        assert!(first.bytes().all(|byte| byte.is_ascii_hexdigit()));
    }

    #[test]
    fn confirmation_input_is_narrow_and_strict() {
        let mut decision = ConfirmationDecisionInput {
            confirmation_id: "a".repeat(43),
            operation_id: Uuid::new_v4().to_string(),
            call_id: Uuid::new_v4().to_string(),
            tool_name: "file_delete".to_string(),
            arguments_digest: "a".repeat(64),
            decision: "allow".to_string(),
        };
        assert!(validate_confirmation_decision(&decision).is_ok());

        decision.tool_name = "file_delete\r\nHost: attacker".to_string();
        assert!(validate_confirmation_decision(&decision).is_err());
    }

    #[test]
    fn confirmation_http_parser_accepts_only_a_real_http_status_line() {
        assert_eq!(parse_http_status("HTTP/1.1 200 OK\r"), Some(200));
        assert_eq!(parse_http_status("HTTP/1.0 409 Conflict"), Some(409));
        assert_eq!(parse_http_status("attacker 200 OK"), None);
        assert_eq!(parse_http_status("HTTP/1.1 2000 Invalid"), None);
    }
}
