//! Desktop shell: starts the Python backend as a sidecar on a free local port and
//! exposes its URL to the webview. All creative work happens in the backend.

use std::net::TcpListener;
use std::sync::Mutex;

use tauri::Manager;
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

struct Backend {
    url: String,
    child: Mutex<Option<CommandChild>>,
}

#[tauri::command]
fn backend_url(state: tauri::State<'_, Backend>) -> String {
    state.url.clone()
}

fn free_port() -> u16 {
    TcpListener::bind("127.0.0.1:0")
        .and_then(|l| l.local_addr())
        .map(|a| a.port())
        .unwrap_or(8765)
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            // Development / external backend: DAEDELUS_BACKEND_URL=http://127.0.0.1:8765
            if let Ok(url) = std::env::var("DAEDELUS_BACKEND_URL") {
                app.manage(Backend { url, child: Mutex::new(None) });
                return Ok(());
            }
            let port = free_port();
            let workspace = app.path().app_data_dir()?.join("workspace");
            std::fs::create_dir_all(&workspace)?;
            let (mut rx, child) = app
                .shell()
                .sidecar("daedelus-server")?
                .args([
                    "--workspace",
                    &workspace.to_string_lossy(),
                    "serve",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    &port.to_string(),
                    "--parent-pid",
                    &std::process::id().to_string(),
                ])
                .spawn()?;
            tauri::async_runtime::spawn(async move {
                while let Some(event) = rx.recv().await {
                    match event {
                        CommandEvent::Stdout(line) | CommandEvent::Stderr(line) => {
                            eprintln!("[backend] {}", String::from_utf8_lossy(&line).trim_end());
                        }
                        CommandEvent::Terminated(status) => {
                            eprintln!("[backend] terminated: {:?}", status);
                        }
                        _ => {}
                    }
                }
            });
            app.manage(Backend {
                url: format!("http://127.0.0.1:{port}"),
                child: Mutex::new(Some(child)),
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![backend_url])
        .build(tauri::generate_context!())
        .expect("error while building Daedelus Studio")
        .run(|app, event| {
            if let tauri::RunEvent::Exit = event {
                if let Some(state) = app.try_state::<Backend>() {
                    if let Some(child) = state.child.lock().unwrap().take() {
                        let _ = child.kill();
                    }
                }
            }
        });
}
