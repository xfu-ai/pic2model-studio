#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use aipic_to_model_host::SidecarState;
use image::codecs::bmp::BmpEncoder;
use image::codecs::png::{CompressionType, FilterType, PngEncoder};
use image::{imageops, ColorType, ImageEncoder, RgbaImage};
use screenshots::Screen;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::io::{Read, Write};
use std::net::TcpListener;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::{mpsc, Mutex};
use std::time::{Duration, Instant};
use tauri::menu::MenuBuilder;
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{Emitter, Manager, PhysicalPosition, PhysicalSize, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_dialog::DialogExt;
use tauri_plugin_notification::NotificationExt;
use tauri_plugin_opener::OpenerExt;

const MODEL_VIEWER_JS: &[u8] =
    include_bytes!("../resources/model-viewer/model-viewer.min.js");
const SCREEN_CAPTURE_WINDOW_LABEL: &str = "screen-capture-overlay";
const SCREEN_CAPTURE_STANDBY_TOKEN: &str = "__standby__";
const MAX_AGENT_DROP_IMAGES: usize = 8;

#[derive(Default)]
struct AgentDropState(Mutex<Option<String>>);

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
struct AgentDropItem {
    capability_id: String,
    file_name: String,
}

fn is_agent_image_path(path: &Path) -> bool {
    path.is_file()
        && matches!(
            path.extension()
                .and_then(|extension| extension.to_str())
                .map(str::to_ascii_lowercase)
                .as_deref(),
            Some("png" | "jpg" | "jpeg" | "bmp" | "webp")
        )
}

fn authorize_agent_drop(
    state: &SidecarState,
    project_id: &str,
    paths: &[PathBuf],
) -> Vec<AgentDropItem> {
    paths
        .iter()
        .filter(|path| is_agent_image_path(path))
        .take(MAX_AGENT_DROP_IMAGES)
        .filter_map(|path| {
            let file_name = path.file_name()?.to_str()?.to_owned();
            let capability_id = state
                .issue_capability(path, "import", Some(project_id))
                .ok()?;
            Some(AgentDropItem {
                capability_id,
                file_name,
            })
        })
        .collect()
}

#[tauri::command]
fn set_agent_drop_project(
    state: tauri::State<'_, AgentDropState>,
    project_id: Option<String>,
) -> Result<(), String> {
    if project_id.as_deref().is_some_and(str::is_empty) {
        return Err("The active project is not valid.".to_string());
    }
    *state
        .0
        .lock()
        .map_err(|_| "The image drop target is unavailable.".to_string())? = project_id;
    Ok(())
}

fn python_has_provider_tls(python: &Path) -> bool {
    Command::new(python)
        .args([
            "-c",
            "import ssl,sys; sys.exit(0 if ssl.OPENSSL_VERSION_INFO[:2] != (3, 5) else 1)",
        ])
        .output()
        .is_ok_and(|output| output.status.success())
}

fn provider_compatible_python() -> PathBuf {
    if let Some(configured) = std::env::var_os("AIPIC_TO_MODEL_PYTHON") {
        let configured = PathBuf::from(configured);
        let controlled_e2e =
            std::env::var("AIPIC_CONTROLLED_E2E").ok().as_deref() == Some("1");
        if controlled_e2e || python_has_provider_tls(&configured) {
            return configured;
        }
    }
    #[cfg(windows)]
    {
        let selected = Command::new("py")
            .args([
                "-3.14",
                "-c",
                "import ssl,sys; print(sys.executable if ssl.OPENSSL_VERSION_INFO[:2] != (3, 5) else '')",
            ])
            .output();
        if let Ok(output) = selected {
            if output.status.success() {
                let candidate = PathBuf::from(String::from_utf8_lossy(&output.stdout).trim());
                if candidate.is_file() {
                    return candidate;
                }
            }
        }
        PathBuf::from("python")
    }
    #[cfg(not(windows))]
    {
        PathBuf::from("python3")
    }
}

/// Test-only fixture authority for controlled WebView2 E2E. It is disabled in
/// release builds and requires two explicit environment variables, so
/// normal desktop operation still always opens the native picker.
fn controlled_e2e_fixture(name: &str) -> Option<PathBuf> {
    if !cfg!(debug_assertions) || std::env::var("AIPIC_CONTROLLED_E2E").ok().as_deref() != Some("1")
    {
        return None;
    }
    let root = std::env::var_os("AIPIC_CONTROLLED_E2E_FIXTURE_ROOT")?;
    let root = PathBuf::from(root);
    let candidate = root.join(name);
    candidate.exists().then_some(candidate)
}

fn controlled_e2e_capability(
    state: &SidecarState,
    name: &str,
    operation: &str,
    project_id: Option<&str>,
) -> Option<Result<Option<String>, String>> {
    let path = controlled_e2e_fixture(name)?;
    Some(
        state
            .issue_capability(&path, operation, project_id)
            .map(Some)
            .map_err(|error| error.to_string()),
    )
}

#[tauri::command]
fn get_renderer_session(
    state: tauri::State<'_, SidecarState>,
) -> aipic_to_model_host::RendererSession {
    state.renderer_session()
}

async fn choose_folder_capability(
    app: tauri::AppHandle,
    state: tauri::State<'_, SidecarState>,
    operation: String,
    project_id: Option<String>,
) -> Result<Option<String>, String> {
    let selected =
        tauri::async_runtime::spawn_blocking(move || app.dialog().file().blocking_pick_folder())
            .await
            .map_err(|_| "The folder picker could not be opened.".to_string())?;
    match selected.and_then(|file| file.into_path().ok()) {
        Some(path) => state
            .issue_capability(&path, &operation, project_id.as_deref())
            .map(Some)
            .map_err(|error| error.to_string()),
        None => Ok(None),
    }
}

async fn choose_file_capability(
    app: tauri::AppHandle,
    state: tauri::State<'_, SidecarState>,
    operation: String,
    project_id: String,
) -> Result<Option<String>, String> {
    let mut dialog = app
        .dialog()
        .file()
        .set_title("Select an image for AIPicToModel")
        .add_filter("Images", &["png", "jpg", "jpeg", "bmp", "webp"]);
    if let Some(window) = app.get_webview_window("main") {
        dialog = dialog.set_parent(&window);
    }
    let selected = tauri::async_runtime::spawn_blocking(move || dialog.blocking_pick_file())
        .await
        .map_err(|_| "The file picker could not be opened.".to_string())?;
    match selected.and_then(|file| file.into_path().ok()) {
        Some(path) => state
            .issue_capability(&path, &operation, Some(&project_id))
            .map(Some)
            .map_err(|error| error.to_string()),
        None => Ok(None),
    }
}

#[tauri::command]
async fn choose_project_directory(
    state: tauri::State<'_, SidecarState>,
    app: tauri::AppHandle,
) -> Result<Option<String>, String> {
    if let Some(result) = controlled_e2e_capability(&state, "project", "create", None) {
        return result;
    }
    choose_folder_capability(app, state, "create".to_owned(), None).await
}

#[tauri::command]
async fn choose_existing_project_directory(
    state: tauri::State<'_, SidecarState>,
    app: tauri::AppHandle,
) -> Result<Option<String>, String> {
    if let Some(result) = controlled_e2e_capability(&state, "project", "open", None) {
        return result;
    }
    choose_folder_capability(app, state, "open".to_owned(), None).await
}

#[tauri::command]
fn choose_recent_project(
    state: tauri::State<'_, SidecarState>,
    recent_project_id: String,
) -> Result<Option<String>, String> {
    state
        .issue_recent_project_capability(&recent_project_id)
        .map(Some)
        .map_err(|error| error.to_string())
}

#[tauri::command]
async fn choose_import_image(
    state: tauri::State<'_, SidecarState>,
    app: tauri::AppHandle,
    project_id: String,
) -> Result<Option<String>, String> {
    if let Some(result) =
        controlled_e2e_capability(&state, "source-a.png", "import", Some(&project_id))
    {
        return result;
    }
    choose_file_capability(app, state, "import".to_owned(), project_id).await
}

#[tauri::command]
async fn choose_import_glb(
    state: tauri::State<'_, SidecarState>,
    app: tauri::AppHandle,
    project_id: String,
) -> Result<Option<String>, String> {
    if let Some(result) =
        controlled_e2e_capability(&state, "fixture-model.glb", "import", Some(&project_id))
    {
        return result;
    }
    let selected = tauri::async_runtime::spawn_blocking(move || {
        app.dialog()
            .file()
            .add_filter("GLB models", &["glb"])
            .blocking_pick_file()
    })
    .await
    .map_err(|_| "The file picker could not be opened.".to_string())?;
    match selected.and_then(|file| file.into_path().ok()) {
        Some(path) => state
            .issue_capability(&path, "import", Some(&project_id))
            .map(Some)
            .map_err(|error| error.to_string()),
        None => Ok(None),
    }
}

struct PendingScreenCapture {
    preview_path: PathBuf,
    source_path: PathBuf,
    source_width: u32,
    source_height: u32,
    project_id: String,
    sender: mpsc::SyncSender<Result<String, String>>,
}

#[derive(Default)]
struct ScreenCaptureState {
    pending: Mutex<HashMap<String, PendingScreenCapture>>,
}

#[derive(Deserialize)]
struct NormalizedCaptureRect {
    x: f64,
    y: f64,
    width: f64,
    height: f64,
}

fn finish_screen_capture(
    app: &tauri::AppHandle,
    token: &str,
    outcome: Result<String, String>,
    close_overlay: bool,
) {
    let capture_state = app.state::<ScreenCaptureState>();
    let pending = capture_state
        .pending
        .lock()
        .ok()
        .and_then(|mut captures| captures.remove(token));
    if let Some(pending) = pending {
        let _ = std::fs::remove_file(&pending.preview_path);
        let _ = std::fs::remove_file(&pending.source_path);
        let _ = pending.sender.send(outcome);
    }
    if close_overlay {
        reset_screen_capture_overlay(app);
    }
    show_main_window(app);
}

#[tauri::command]
fn screen_capture_preview(
    state: tauri::State<'_, ScreenCaptureState>,
    token: String,
) -> Result<tauri::ipc::Response, String> {
    let captures = state
        .pending
        .lock()
        .map_err(|_| "The screen capture is no longer available.".to_string())?;
    let pending = captures
        .get(&token)
        .ok_or_else(|| "The screen capture is no longer available.".to_string())?;
    let bytes = std::fs::read(&pending.preview_path)
        .map_err(|_| "The screen capture preview could not be loaded.".to_string())?;
    Ok(tauri::ipc::Response::new(bytes))
}

fn save_fast_png(path: &Path, image: &RgbaImage) -> Result<(), image::ImageError> {
    let output = std::io::BufWriter::new(std::fs::File::create(path)?);
    PngEncoder::new_with_quality(output, CompressionType::Fast, FilterType::Sub).write_image(
        image.as_raw(),
        image.width(),
        image.height(),
        ColorType::Rgba8,
    )
}

fn save_fast_bmp(path: &Path, image: &RgbaImage) -> Result<(), image::ImageError> {
    let mut output = std::io::BufWriter::new(std::fs::File::create(path)?);
    BmpEncoder::new(&mut output).encode(
        image.as_raw(),
        image.width(),
        image.height(),
        ColorType::Rgba8,
    )
}

fn save_screen_preview(path: &Path, source: &RgbaImage) -> Result<(), image::ImageError> {
    const MAX_PREVIEW_WIDTH: u32 = 2560;
    const MAX_PREVIEW_HEIGHT: u32 = 1440;
    let scale = (f64::from(MAX_PREVIEW_WIDTH) / f64::from(source.width()))
        .min(f64::from(MAX_PREVIEW_HEIGHT) / f64::from(source.height()))
        .min(1.0);
    if scale >= 1.0 {
        save_fast_bmp(path, source)
    } else {
        let width = (f64::from(source.width()) * scale).round().max(1.0) as u32;
        let height = (f64::from(source.height()) * scale).round().max(1.0) as u32;
        let preview = imageops::resize(source, width, height, imageops::FilterType::Nearest);
        save_fast_bmp(path, &preview)
    }
}

#[tauri::command]
fn complete_screen_capture(
    app: tauri::AppHandle,
    sidecar: tauri::State<'_, SidecarState>,
    capture_state: tauri::State<'_, ScreenCaptureState>,
    token: String,
    rect: NormalizedCaptureRect,
) -> Result<(), String> {
    if !rect.x.is_finite()
        || !rect.y.is_finite()
        || !rect.width.is_finite()
        || !rect.height.is_finite()
        || rect.x < 0.0
        || rect.y < 0.0
        || rect.width <= 0.001
        || rect.height <= 0.001
        || rect.x + rect.width > 1.000_001
        || rect.y + rect.height > 1.000_001
    {
        return Err("Select a valid screen region before confirming.".to_string());
    }
    let pending = {
        let mut captures = capture_state
            .pending
            .lock()
            .map_err(|_| "The screen capture is no longer available.".to_string())?;
        captures
            .remove(&token)
            .ok_or_else(|| "The screen capture is no longer available.".to_string())?
    };
    let result = (|| {
        let source_bytes = std::fs::read(&pending.source_path)
            .map_err(|_| "The full-resolution screen capture could not be loaded.".to_string())?;
        let source = RgbaImage::from_raw(
            pending.source_width,
            pending.source_height,
            source_bytes,
        )
        .ok_or_else(|| "The full-resolution screen capture is invalid.".to_string())?;
        let source_width = source.width();
        let source_height = source.height();
        let x = ((rect.x * f64::from(source_width)).floor() as u32)
            .min(source_width.saturating_sub(1));
        let y = ((rect.y * f64::from(source_height)).floor() as u32)
            .min(source_height.saturating_sub(1));
        let width = ((rect.width * f64::from(source_width)).round() as u32)
            .max(1)
            .min(source_width - x);
        let height = ((rect.height * f64::from(source_height)).round() as u32)
            .max(1)
            .min(source_height - y);
        let cropped = imageops::crop_imm(&source, x, y, width, height).to_image();
        let staging = pending
            .preview_path
            .parent()
            .ok_or_else(|| "The selected screen region could not be prepared.".to_string())?;
        let cropped_path = staging.join(format!("capture-region-{}.png", random_staging_name()));
        save_fast_png(&cropped_path, &cropped)
            .map_err(|_| "The selected screen region could not be saved.".to_string())?;
        sidecar
            .issue_capability(&cropped_path, "import", Some(&pending.project_id))
            .map_err(|_| {
                let _ = std::fs::remove_file(&cropped_path);
                "The selected screen region could not be authorized.".to_string()
            })
    })();
    let _ = std::fs::remove_file(&pending.preview_path);
    let _ = std::fs::remove_file(&pending.source_path);
    let command_result = result.as_ref().map(|_| ()).map_err(Clone::clone);
    let _ = pending.sender.send(result);
    reset_screen_capture_overlay(&app);
    show_main_window(&app);
    command_result
}

#[tauri::command]
fn cancel_screen_capture(app: tauri::AppHandle, token: String) {
    finish_screen_capture(
        &app,
        &token,
        Err("Screen capture cancelled.".to_string()),
        true,
    );
}

fn get_or_create_screen_capture_overlay(
    app: &tauri::AppHandle,
) -> Result<tauri::WebviewWindow, String> {
    if let Some(overlay) = app.get_webview_window(SCREEN_CAPTURE_WINDOW_LABEL) {
        return Ok(overlay);
    }
    let main_browser_args = app
        .config()
        .app
        .windows
        .iter()
        .find(|window| window.label == "main")
        .and_then(|window| window.additional_browser_args.clone());
    let mut overlay_builder = WebviewWindowBuilder::new(
        app,
        SCREEN_CAPTURE_WINDOW_LABEL,
        WebviewUrl::App(
            format!("index.html?screen-capture={SCREEN_CAPTURE_STANDBY_TOKEN}").into(),
        ),
    );
    if let Some(browser_args) = main_browser_args.as_deref() {
        overlay_builder = overlay_builder.additional_browser_args(browser_args);
    }
    overlay_builder
        .title("AIPicToModel screen capture")
        .decorations(false)
        .always_on_top(true)
        .skip_taskbar(true)
        .shadow(false)
        .resizable(false)
        .closable(false)
        .visible(false)
        .build()
        .map_err(|_| "The screenshot selector could not be opened.".to_string())
}

fn reset_screen_capture_overlay(app: &tauri::AppHandle) {
    if let Some(overlay) = app.get_webview_window(SCREEN_CAPTURE_WINDOW_LABEL) {
        let _ = overlay.hide();
        let standby_url =
            format!("index.html?screen-capture={SCREEN_CAPTURE_STANDBY_TOKEN}");
        if let Ok(script_url) = serde_json::to_string(&standby_url) {
            let _ = overlay.eval(format!("window.location.replace({script_url})"));
        }
    }
}

fn open_screen_capture_overlay(
    app: tauri::AppHandle,
    token: String,
    min_x: i32,
    min_y: i32,
    width: u32,
    height: u32,
    source_width: u32,
    source_height: u32,
) -> Result<(), String> {
    let overlay = get_or_create_screen_capture_overlay(&app)?;
    overlay
        .set_position(PhysicalPosition::new(min_x, min_y))
        .and_then(|_| overlay.set_size(PhysicalSize::new(width, height)))
        .map_err(|_| "The screenshot selector could not cover the displays.".to_string())?;
    let capture_url = format!(
        "index.html?screen-capture={token}&source-width={source_width}&source-height={source_height}"
    );
    let script_url = serde_json::to_string(&capture_url)
        .map_err(|_| "The screenshot selector could not be prepared.".to_string())?;
    overlay
        .eval(format!("window.location.replace({script_url})"))
        .map_err(|_| "The screenshot selector could not be prepared.".to_string())?;
    overlay
        .show()
        .and_then(|_| overlay.set_focus())
        .map_err(|_| "The screenshot selector could not be shown.".to_string())
}

/// Capture every attached display into one physical-pixel snapshot, display that
/// snapshot in a native fullscreen selection window, and return only the
/// selected region through a short-lived import capability. Paths never cross
/// the native/renderer boundary.
#[tauri::command]
async fn capture_screen_capability(
    app: tauri::AppHandle,
    capture_state: tauri::State<'_, ScreenCaptureState>,
    project_id: String,
) -> Result<String, String> {
    let window = app.get_webview_window("main");
    if let Some(window) = &window {
        let _ = window.hide();
    }
    std::thread::sleep(std::time::Duration::from_millis(120));
    let prepared = (|| {
        let screens = Screen::all().map_err(|_| "Unable to enumerate displays.".to_string())?;
        if screens.is_empty() {
            return Err("No display is available for capture.".to_string());
        }
        let min_x = screens
            .iter()
            .map(|screen| screen.display_info.x)
            .min()
            .unwrap_or(0);
        let min_y = screens
            .iter()
            .map(|screen| screen.display_info.y)
            .min()
            .unwrap_or(0);
        let max_x = screens
            .iter()
            .map(|screen| screen.display_info.x + screen.display_info.width as i32)
            .max()
            .unwrap_or(0);
        let max_y = screens
            .iter()
            .map(|screen| screen.display_info.y + screen.display_info.height as i32)
            .max()
            .unwrap_or(0);
        let width =
            u32::try_from(max_x - min_x).map_err(|_| "Invalid display geometry.".to_string())?;
        let height =
            u32::try_from(max_y - min_y).map_err(|_| "Invalid display geometry.".to_string())?;
        let staging = std::env::temp_dir()
            .join("AIPicToModel")
            .join("screenshots");
        std::fs::create_dir_all(&staging)
            .map_err(|_| "The screenshot could not be prepared.".to_string())?;
        let preview_path =
            staging.join(format!("capture-preview-{}.bmp", random_staging_name()));
        let source_path =
            staging.join(format!("capture-source-{}.rgba", random_staging_name()));
        let captured = std::thread::scope(|scope| {
            let handles = screens
                .into_iter()
                .map(|screen| {
                    scope.spawn(move || {
                        let x = screen.display_info.x;
                        let y = screen.display_info.y;
                        screen
                            .capture()
                            .map(|image| (x, y, image))
                            .map_err(|_| "Screen capture failed.".to_string())
                    })
                })
                .collect::<Vec<_>>();
            handles
                .into_iter()
                .map(|handle| {
                    handle
                        .join()
                        .map_err(|_| "Screen capture stopped unexpectedly.".to_string())?
                })
                .collect::<Result<Vec<_>, String>>()
        })?;
        let mut source = RgbaImage::new(width, height);
        for (screen_x, screen_y, image) in captured {
            let x = i64::from(screen_x - min_x);
            let y = i64::from(screen_y - min_y);
            imageops::overlay(&mut source, &image, x, y);
        }
        save_screen_preview(&preview_path, &source)
            .map_err(|_| "The screenshot preview could not be saved.".to_string())?;
        std::fs::write(&source_path, source.as_raw()).map_err(|_| {
            let _ = std::fs::remove_file(&preview_path);
            let _ = std::fs::remove_file(&source_path);
            "The full-resolution screenshot could not be saved.".to_string()
        })?;
        Ok((
            preview_path,
            source_path,
            source.width(),
            source.height(),
            min_x,
            min_y,
            width,
            height,
        ))
    })();
    let (
        preview_path,
        source_path,
        source_width,
        source_height,
        min_x,
        min_y,
        width,
        height,
    ) = match prepared {
        Ok(prepared) => prepared,
        Err(error) => {
            show_main_window(&app);
            return Err(error);
        }
    };
    let token = random_staging_name();
    let (sender, receiver) = mpsc::sync_channel(1);
    {
        let mut pending = match capture_state.pending.lock() {
            Ok(pending) => pending,
            Err(_) => {
                let _ = std::fs::remove_file(&preview_path);
                let _ = std::fs::remove_file(&source_path);
                show_main_window(&app);
                return Err("The screenshot selector could not be prepared.".to_string());
            }
        };
        pending.insert(
            token.clone(),
            PendingScreenCapture {
                preview_path,
                source_path,
                source_width,
                source_height,
                project_id,
                sender,
            },
        );
    }
    let (overlay_sender, overlay_receiver) = mpsc::sync_channel(1);
    let overlay_app = app.clone();
    let overlay_token = token.clone();
    if app
        .run_on_main_thread(move || {
            let result = open_screen_capture_overlay(
                overlay_app,
                overlay_token,
                min_x,
                min_y,
                width,
                height,
                source_width,
                source_height,
            );
            let _ = overlay_sender.send(result);
        })
        .is_err()
    {
        finish_screen_capture(
            &app,
            &token,
            Err("The screenshot selector could not be scheduled.".to_string()),
            true,
        );
        return Err("The screenshot selector could not be scheduled.".to_string());
    }
    let overlay_result = tauri::async_runtime::spawn_blocking(move || {
        overlay_receiver.recv_timeout(Duration::from_secs(10))
    })
    .await
    .map_err(|_| "The screenshot selector stopped unexpectedly.".to_string())?;
    match overlay_result {
        Ok(Ok(())) => {}
        Ok(Err(error)) => {
            finish_screen_capture(&app, &token, Err(error.clone()), true);
            return Err(error);
        }
        Err(_) => {
            finish_screen_capture(
                &app,
                &token,
                Err("The screenshot selector did not open in time.".to_string()),
                true,
            );
            return Err("The screenshot selector did not open in time.".to_string());
        }
    }
    let outcome = tauri::async_runtime::spawn_blocking(move || {
        receiver.recv_timeout(Duration::from_secs(300))
    })
    .await
    .map_err(|_| "The screenshot selector stopped unexpectedly.".to_string())?;
    match outcome {
        Ok(result) => result,
        Err(_) => {
            finish_screen_capture(
                &app,
                &token,
                Err("Screen capture timed out.".to_string()),
                true,
            );
            Err("Screen capture timed out.".to_string())
        }
    }
}

#[tauri::command]
async fn choose_export_directory(
    state: tauri::State<'_, SidecarState>,
    app: tauri::AppHandle,
    project_id: String,
) -> Result<Option<String>, String> {
    if let Some(result) = controlled_e2e_capability(&state, "export", "export", Some(&project_id)) {
        return result;
    }
    choose_folder_capability(app, state, "export".to_owned(), Some(project_id)).await
}

#[tauri::command]
async fn choose_diagnostics_export_directory(
    state: tauri::State<'_, SidecarState>,
    app: tauri::AppHandle,
    project_id: String,
) -> Result<Option<String>, String> {
    if let Some(result) =
        controlled_e2e_capability(&state, "export", "diagnostic_export", Some(&project_id))
    {
        return result;
    }
    choose_folder_capability(app, state, "diagnostic_export".to_owned(), Some(project_id)).await
}

/// The renderer can report only a constrained terminal state.  Provider text,
/// asset names, paths, and error details are deliberately excluded from OS notifications.
#[tauri::command]
fn notify_job_terminal(app: tauri::AppHandle, status: String) -> Result<(), String> {
    let body = match status.as_str() {
        "succeeded" => "A background task has completed.",
        "failed" => "A background task failed. Open Task Center for details.",
        "cancelled" => "A background task was cancelled.",
        "interrupted" => "A background task was interrupted. Open Task Center for details.",
        _ => return Err("The task status is not terminal.".to_string()),
    };
    app.notification()
        .builder()
        .title("AIPicToModel")
        .body(body)
        .show()
        .map_err(|_| "The desktop notification could not be shown.".to_string())
}

/// Open a managed GLB in the default browser without ever passing it a local
/// filesystem path. The short-lived loopback server serves only two fixed,
/// token-scoped resources and exits after a bounded idle period.
#[tauri::command]
fn open_model_browser_preview(app: tauri::AppHandle, bytes: Vec<u8>) -> Result<(), String> {
    const MAX_MODEL_BYTES: usize = 100 * 1024 * 1024;
    if bytes.is_empty() || bytes.len() > MAX_MODEL_BYTES || !bytes.starts_with(b"glTF") {
        return Err("The managed GLB preview is not valid.".to_string());
    }
    let listener = TcpListener::bind("127.0.0.1:0")
        .map_err(|_| "The browser preview service could not start.".to_string())?;
    listener
        .set_nonblocking(true)
        .map_err(|_| "The browser preview service could not start.".to_string())?;
    let port = listener
        .local_addr()
        .map_err(|_| "The browser preview service could not start.".to_string())?
        .port();
    let token = random_staging_name();
    let browser_url = format!("http://127.0.0.1:{port}/{token}/index.html");
    std::thread::spawn(move || serve_browser_preview(listener, token, bytes));
    app.opener()
        .open_url(browser_url, None::<&str>)
        .map_err(|_| "The default browser could not be opened.".to_string())
}

fn serve_browser_preview(listener: TcpListener, token: String, glb: Vec<u8>) {
    let deadline = Instant::now() + Duration::from_secs(15 * 60);
    while Instant::now() < deadline {
        match listener.accept() {
            Ok((mut stream, _)) => {
                let mut request = [0_u8; 4096];
                let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
                let read = stream.read(&mut request).unwrap_or(0);
                let path = std::str::from_utf8(&request[..read])
                    .ok()
                    .and_then(|text| text.lines().next())
                    .and_then(|line| line.split_whitespace().nth(1))
                    .unwrap_or("");
                if path == format!("/{token}/index.html") {
                    write_preview_response(
                        &mut stream,
                        "text/html; charset=utf-8",
                        preview_html().as_bytes(),
                    );
                } else if path == format!("/{token}/model-viewer.min.js") {
                    write_preview_response(
                        &mut stream,
                        "text/javascript; charset=utf-8",
                        MODEL_VIEWER_JS,
                    );
                } else if path == format!("/{token}/model.glb") {
                    write_preview_response(&mut stream, "model/gltf-binary", &glb);
                } else {
                    let _ = stream.write_all(
                        b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n",
                    );
                }
            }
            Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                std::thread::sleep(Duration::from_millis(50));
            }
            Err(_) => break,
        }
    }
}

fn write_preview_response(stream: &mut std::net::TcpStream, content_type: &str, body: &[u8]) {
    let headers = format!(
        "HTTP/1.1 200 OK\r\nContent-Type: {content_type}\r\nContent-Length: {}\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n",
        body.len()
    );
    let _ = stream.write_all(headers.as_bytes());
    let _ = stream.write_all(body);
}

fn preview_html() -> &'static str {
    r#"<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>AIPicToModel model preview</title><style>html,body,model-viewer{width:100%;height:100%;margin:0;background:#111827}model-viewer{--poster-color:#111827}</style><script type="module" src="./model-viewer.min.js"></script><model-viewer src="./model.glb" camera-controls auto-rotate shadow-intensity="1" exposure="1" alt="3D model"></model-viewer></html>"#
}

/// The webview supplies file bytes, never a local path. The host creates a private
/// staging file and returns the same single-use capability used by native pickers.
#[tauri::command]
fn stage_dropped_file(
    state: tauri::State<'_, SidecarState>,
    project_id: String,
    asset_kind: String,
    file_name: String,
    bytes: Vec<u8>,
) -> Result<String, String> {
    const MAX_DROP_BYTES: usize = 200 * 1024 * 1024;
    if bytes.is_empty()
        || bytes.len() > MAX_DROP_BYTES
        || Path::new(&file_name)
            .file_name()
            .and_then(|name| name.to_str())
            != Some(file_name.as_str())
    {
        return Err("The dropped file is not valid.".to_string());
    }
    let extension = Path::new(&file_name)
        .extension()
        .and_then(|item| item.to_str())
        .unwrap_or("")
        .to_ascii_lowercase();
    let accepted = match asset_kind.as_str() {
        "source_image" => matches!(extension.as_str(), "png" | "jpg" | "jpeg" | "bmp" | "webp"),
        "glb" => extension == "glb",
        _ => false,
    };
    if !accepted {
        return Err("Only PNG, JPG, BMP, WEBP, or GLB files can be dropped here.".to_string());
    }
    let staging = std::env::temp_dir()
        .join("AIPicToModel")
        .join("drop-staging");
    std::fs::create_dir_all(&staging)
        .map_err(|_| "The dropped file could not be prepared.".to_string())?;
    let staged_path = staging.join(format!("{}-{}", random_staging_name(), file_name));
    std::fs::write(&staged_path, bytes)
        .map_err(|_| "The dropped file could not be prepared.".to_string())?;
    state
        .issue_capability(&staged_path, "import", Some(&project_id))
        .map_err(|_| {
            let _ = std::fs::remove_file(&staged_path);
            "The dropped file could not be authorized.".to_string()
        })
}

fn random_staging_name() -> String {
    let mut bytes = [0_u8; 16];
    rand::RngCore::fill_bytes(&mut rand::rng(), &mut bytes);
    base64::Engine::encode(&base64::engine::general_purpose::URL_SAFE_NO_PAD, bytes)
}

#[cfg(test)]
mod tests {
    use super::{is_agent_image_path, preview_html, random_staging_name, MODEL_VIEWER_JS};

    #[test]
    fn browser_model_preview_uses_only_the_embedded_viewer() {
        let html = preview_html();
        assert!(html.contains("./model-viewer.min.js"));
        assert!(html.contains("./model.glb"));
        assert!(!html.contains("unpkg.com"));
        assert!(!html.contains("https://"));
        assert!(!MODEL_VIEWER_JS.is_empty());
    }

    #[test]
    fn agent_drop_accepts_only_supported_existing_images() {
        let fixture =
            std::env::temp_dir().join(format!("agent-drop-{}.PNG", random_staging_name()));
        std::fs::write(&fixture, b"fixture").expect("create image path fixture");
        assert!(is_agent_image_path(&fixture));
        assert!(!is_agent_image_path(std::path::Path::new(
            "not-an-existing-image.png"
        )));
        let unsupported =
            std::env::temp_dir().join(format!("agent-drop-{}.svg", random_staging_name()));
        std::fs::write(&unsupported, b"fixture").expect("create unsupported path fixture");
        assert!(!is_agent_image_path(&unsupported));
        std::fs::remove_file(fixture).expect("remove image path fixture");
        std::fs::remove_file(unsupported).expect("remove unsupported path fixture");
    }
}

fn main() {
    tauri::Builder::default()
        .manage(ScreenCaptureState::default())
        .manage(AgentDropState::default())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_opener::init())
        .invoke_handler(tauri::generate_handler![
            get_renderer_session,
            choose_project_directory,
            choose_existing_project_directory,
            choose_recent_project,
            choose_import_image,
            choose_import_glb,
            capture_screen_capability,
            screen_capture_preview,
            complete_screen_capture,
            cancel_screen_capture,
            choose_export_directory,
            choose_diagnostics_export_directory,
            notify_job_terminal,
            open_model_browser_preview,
            stage_dropped_file,
            set_agent_drop_project,
        ])
        .setup(|app| {
            let controlled_e2e = cfg!(debug_assertions)
                && std::env::var("AIPIC_CONTROLLED_E2E").ok().as_deref() == Some("1");
            if let Some(window) = app.get_webview_window("main") {
                window.set_title(if controlled_e2e {
                    "AIPicToModel controlled E2E"
                } else {
                    "AIPicToModel"
                })?;
            }
            let app_data = if cfg!(debug_assertions)
                && controlled_e2e
            {
                std::env::var_os("AIPIC_CONTROLLED_E2E_APP_DATA")
                    .map(PathBuf::from)
                    .ok_or_else(|| {
                        "The controlled E2E app-data directory is missing.".to_string()
                    })?
            } else {
                app.path()
                    .app_data_dir()
                    .map_err(|error| error.to_string())?
            };
            std::fs::create_dir_all(&app_data).map_err(|error| error.to_string())?;
            let resources = app
                .path()
                .resource_dir()
                .map_err(|error| error.to_string())?;
            let resource_file = |relative: &Path| {
                let direct = resources.join(relative);
                if direct.is_file() {
                    direct
                } else {
                    resources.join("resources").join(relative)
                }
            };
            let configured_binary =
                std::env::var_os("AIPIC_TO_MODEL_SIDECAR_BIN").map(PathBuf::from);
            let packaged_binary = resource_file(
                Path::new("sidecar")
                    .join(if cfg!(windows) {
                        "aipic-to-model-sidecar.exe"
                    } else {
                        "aipic-to-model-sidecar"
                    })
                    .as_path(),
            );
            let app_db = app_data.join("app.sqlite3");
            let workspace_source_available = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                .join("..")
                .join("..")
                .join("src")
                .is_dir();
            let force_workspace_python = cfg!(debug_assertions)
                && std::env::var("AIPIC_TO_MODEL_FORCE_PYTHON")
                    .ok()
                    .as_deref()
                    == Some("1");
            // Debug and controlled runs exercise the workspace Python source.
            // Release builds always prefer the bundled sidecar so a portable
            // copy cannot accidentally depend on a developer workstation.
            let sidecar = if controlled_e2e
                || force_workspace_python
                || (cfg!(debug_assertions) && workspace_source_available)
            {
                SidecarState::launch_python(provider_compatible_python(), app_db)
            } else if let Some(binary) =
                configured_binary.or_else(|| packaged_binary.is_file().then_some(packaged_binary))
            {
                SidecarState::launch_binary(binary, app_db)
            } else {
                SidecarState::launch_python(provider_compatible_python(), app_db)
            }
            .map_err(|error| error.to_string())?;
            app.manage(sidecar);
            // WebView2 window creation is comparatively expensive. Keep one hidden,
            // isolated selector warm so capture latency is dominated by the actual
            // desktop copy instead of a new browser process.
            let _ = get_or_create_screen_capture_overlay(app.handle());
            let tray_menu = MenuBuilder::new(app)
                .text("show", "Show AIPicToModel")
                .separator()
                .text("quit", "Quit")
                .build()?;
            let mut tray = TrayIconBuilder::with_id("main")
                .menu(&tray_menu)
                .tooltip("AIPicToModel")
                .show_menu_on_left_click(false);
            if let Some(icon) = app.default_window_icon().cloned() {
                tray = tray.icon(icon);
            }
            tray.on_menu_event(|app, event| match event.id().as_ref() {
                "show" => show_main_window(app),
                "quit" => app.exit(0),
                _ => {}
            })
            .on_tray_icon_event(|tray, event| {
                if matches!(
                    event,
                    TrayIconEvent::Click {
                        button: MouseButton::Left,
                        button_state: MouseButtonState::Up,
                        ..
                    } | TrayIconEvent::DoubleClick {
                        button: MouseButton::Left,
                        ..
                    }
                ) {
                    show_main_window(tray.app_handle());
                }
            })
            .build(app)?;
            let window = app.get_webview_window("main").expect("main window");
            let window_for_events = window.clone();
            window.on_window_event(move |event| {
                if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                    api.prevent_close();
                    let _ = window_for_events.hide();
                }
                if let tauri::WindowEvent::DragDrop(tauri::DragDropEvent::Drop { paths, .. }) =
                    event
                {
                    let app = window_for_events.app_handle();
                    let project_id = app
                        .state::<AgentDropState>()
                        .0
                        .lock()
                        .ok()
                        .and_then(|active| active.clone());
                    if let Some(project_id) = project_id {
                        let items =
                            authorize_agent_drop(&app.state::<SidecarState>(), &project_id, paths);
                        if !items.is_empty() {
                            let _ = window_for_events.emit("agent-image-drop", items);
                        }
                    }
                }
            });
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("Tauri application error");
}

fn show_main_window(app: &tauri::AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
    }
}
