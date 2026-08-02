use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine as _};
use rand::RngCore;
use serde::{Deserialize, Serialize};
use std::{
    io::{BufRead, BufReader, Read, Write},
    net::TcpStream,
    path::PathBuf,
    process::{Child, Command, Stdio},
    sync::Mutex,
    time::{Duration, Instant},
};
use thiserror::Error;

#[cfg(windows)]
use std::os::windows::io::AsRawHandle;
#[cfg(windows)]
use windows_sys::Win32::{
    Foundation::{CloseHandle, HANDLE},
    System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
        SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    },
};

#[cfg(debug_assertions)]
// WebView2 sends the exact dev-server origin; renderer JavaScript cannot override it.
const TAURI_ORIGIN: &str = "http://127.0.0.1:14200";
#[cfg(not(debug_assertions))]
const TAURI_ORIGIN: &str = "http://tauri.localhost";

fn is_loopback_renderer_origin(origin: &str) -> bool {
    origin
        .strip_prefix("http://127.0.0.1:")
        .and_then(|port| port.parse::<u16>().ok())
        .is_some_and(|port| port > 0)
}

fn renderer_origin() -> Result<String, HostError> {
    #[cfg(debug_assertions)]
    {
        if std::env::var("AIPIC_CONTROLLED_E2E").ok().as_deref() == Some("1") {
            if let Some(origin) = std::env::var_os("AIPIC_CONTROLLED_E2E_RENDERER_ORIGIN") {
                let origin = origin.to_string_lossy().to_string();
                if is_loopback_renderer_origin(&origin) {
                    return Ok(origin);
                }
                return Err(HostError::Start);
            }
        }
    }
    Ok(TAURI_ORIGIN.to_owned())
}

#[derive(Debug, Error)]
pub enum HostError {
    #[error("The local service could not be started.")]
    Start,
    #[error("The local service did not become ready in time.")]
    Timeout,
    #[error("The local service returned an invalid readiness message.")]
    InvalidReady,
    #[error("The native file authorization could not be issued.")]
    Capability,
}

#[derive(Debug, Deserialize)]
struct ReadyMessage {
    event: String,
    port: u16,
}

#[derive(Debug, Serialize, Clone)]
pub struct RendererSession {
    pub base_url: String,
    pub bearer_token: String,
    pub origin: String,
}

pub struct SidecarState {
    child: Mutex<Option<Child>>,
    #[cfg(windows)]
    job: Mutex<Option<KillOnCloseJob>>,
    session: RendererSession,
    host_control_token: String,
}

#[cfg(windows)]
struct KillOnCloseJob {
    handle: HANDLE,
}

#[cfg(windows)]
unsafe impl Send for KillOnCloseJob {}

#[cfg(windows)]
impl KillOnCloseJob {
    fn assign(child: &Child) -> Result<Self, HostError> {
        // SAFETY: all pointers passed to the Win32 APIs are either null or
        // point to a correctly sized initialized structure for the duration of
        // the call. The returned handle is owned by this RAII wrapper.
        unsafe {
            let handle = CreateJobObjectW(std::ptr::null(), std::ptr::null());
            if handle.is_null() {
                return Err(HostError::Start);
            }
            let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
            limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            let configured = SetInformationJobObject(
                handle,
                JobObjectExtendedLimitInformation,
                std::ptr::addr_of_mut!(limits).cast(),
                std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            );
            let assigned = configured != 0
                && AssignProcessToJobObject(handle, child.as_raw_handle() as HANDLE) != 0;
            if !assigned {
                CloseHandle(handle);
                return Err(HostError::Start);
            }
            Ok(Self { handle })
        }
    }
}

#[cfg(windows)]
impl Drop for KillOnCloseJob {
    fn drop(&mut self) {
        // Closing a job configured with KILL_ON_JOB_CLOSE terminates the
        // PyInstaller bootstrapper and every server descendant atomically.
        unsafe {
            CloseHandle(self.handle);
        }
    }
}

impl SidecarState {
    pub fn launch_python(
        python: PathBuf,
        app_db: PathBuf,
    ) -> Result<Self, HostError> {
        let mut command = Command::new(python);
        command.args(["-m", "aipic_to_model.desktop_sidecar", "--app-db"]).arg(&app_db);
        let source_root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..").join("..").join("src");
        if source_root.is_dir() {
            let python_path = match std::env::var_os("PYTHONPATH") {
                Some(existing) if !existing.is_empty() => {
                    let mut paths = vec![source_root];
                    paths.extend(std::env::split_paths(&existing));
                    std::env::join_paths(paths).map_err(|_| HostError::Start)?
                }
                _ => source_root.into_os_string(),
            };
            command.env("PYTHONPATH", python_path);
        }
        Self::launch_command(command)
    }

    pub fn launch_binary(
        binary: PathBuf,
        app_db: PathBuf,
    ) -> Result<Self, HostError> {
        let mut command = Command::new(binary);
        command.args(["--app-db"]).arg(app_db);
        Self::launch_command(command)
    }

    fn launch_command(mut command: Command) -> Result<Self, HostError> {
        let session_token = generate_session_token();
        let host_control_token = generate_session_token();
        let renderer_origin = renderer_origin()?;
        command
            .env("AIPIC_TO_MODEL_SESSION_TOKEN", &session_token)
            .env("AIPIC_TO_MODEL_HOST_CONTROL_TOKEN", &host_control_token)
            .env("AIPIC_TO_MODEL_RENDERER_ORIGIN", &renderer_origin)
            .stdin(Stdio::null())
            .stdout(Stdio::piped());
        #[cfg(debug_assertions)]
        command.stderr(Stdio::inherit());
        #[cfg(not(debug_assertions))]
        command.stderr(Stdio::null());
        #[cfg(debug_assertions)]
        if std::env::var("AIPIC_CONTROLLED_E2E").ok().as_deref() == Some("1") {
            // Propagate the controlled fault budget deliberately. It is only
            // meaningful to the Debug test seam; relying on a wrapper's
            // environment inheritance made the offline/reconnect E2E flaky.
            command.env("AIPIC_CONTROLLED_E2E", "1");
            if let Some(failures) = std::env::var_os("AIPIC_CONTROLLED_E2E_HEALTH_FAILURES") {
                command.env("AIPIC_CONTROLLED_E2E_HEALTH_FAILURES", failures);
            }
        }
        let mut child = command
            .spawn()
            .map_err(|_| HostError::Start)?;
        #[cfg(windows)]
        let job = KillOnCloseJob::assign(&child)?;
        let stdout = child.stdout.take().ok_or(HostError::Start)?;
        let ready = read_ready(stdout)?;
        Ok(Self {
            child: Mutex::new(Some(child)),
            #[cfg(windows)]
            job: Mutex::new(Some(job)),
            session: RendererSession {
                base_url: format!("http://127.0.0.1:{}", ready.port),
                bearer_token: session_token,
                origin: renderer_origin,
            },
            host_control_token,
        })
    }

    pub fn renderer_session(&self) -> RendererSession {
        self.session.clone()
    }

    pub fn issue_capability(
        &self, path: &std::path::Path, operation: &str, project_id: Option<&str>
    ) -> Result<String, HostError> {
        let port = self.session.base_url.rsplit(':').next().ok_or(HostError::Capability)?;
        let request_id = format!("host-{}", generate_session_token());
        let body = serde_json::json!({
            "path": path.to_string_lossy(), "operation": operation, "project_id": project_id,
            "request_id": request_id,
        }).to_string();
        let mut stream = TcpStream::connect(format!("127.0.0.1:{port}")).map_err(|_| HostError::Capability)?;
        let request = format!(
            "POST /v1/host/capabilities HTTP/1.1\r\nHost: 127.0.0.1\r\nAuthorization: Bearer {}\r\nOrigin: {}\r\nX-Host-Control-Token: {}\r\nX-Request-Id: {}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
            self.session.bearer_token, self.session.origin, self.host_control_token, request_id, body.len(), body
        );
        stream.write_all(request.as_bytes()).map_err(|_| HostError::Capability)?;
        let mut response = String::new();
        stream.read_to_string(&mut response).map_err(|_| HostError::Capability)?;
        let (_, json) = response.split_once("\r\n\r\n").ok_or(HostError::Capability)?;
        let parsed: serde_json::Value = serde_json::from_str(json).map_err(|_| HostError::Capability)?;
        parsed.get("capability_id").and_then(serde_json::Value::as_str).map(str::to_owned).ok_or(HostError::Capability)
    }

    pub fn issue_recent_project_capability(&self, recent_project_id: &str) -> Result<String, HostError> {
        let port = self.session.base_url.rsplit(':').next().ok_or(HostError::Capability)?;
        let request_id = format!("host-{}", generate_session_token());
        let body = serde_json::json!({
            "recent_project_id": recent_project_id, "request_id": request_id,
        }).to_string();
        let mut stream = TcpStream::connect(format!("127.0.0.1:{port}")).map_err(|_| HostError::Capability)?;
        let request = format!(
            "POST /v1/host/recent-capabilities HTTP/1.1\r\nHost: 127.0.0.1\r\nAuthorization: Bearer {}\r\nOrigin: {}\r\nX-Host-Control-Token: {}\r\nX-Request-Id: {}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
            self.session.bearer_token, self.session.origin, self.host_control_token, request_id, body.len(), body
        );
        stream.write_all(request.as_bytes()).map_err(|_| HostError::Capability)?;
        let mut response = String::new();
        stream.read_to_string(&mut response).map_err(|_| HostError::Capability)?;
        let (_, json) = response.split_once("\r\n\r\n").ok_or(HostError::Capability)?;
        let parsed: serde_json::Value = serde_json::from_str(json).map_err(|_| HostError::Capability)?;
        parsed.get("capability_id").and_then(serde_json::Value::as_str).map(str::to_owned).ok_or(HostError::Capability)
    }

    pub fn shutdown(&self) {
        if let Ok(mut guard) = self.child.lock() {
            if let Some(child) = guard.as_mut() {
                #[cfg(windows)]
                {
                    if let Ok(mut job) = self.job.lock() {
                        // Dropping the job first terminates the entire tree.
                        *job = None;
                    }
                }
                let _ = child.kill();
                let _ = child.wait();
            }
            *guard = None;
        }
    }
}

impl Drop for SidecarState {
    fn drop(&mut self) {
        self.shutdown();
    }
}

fn generate_session_token() -> String {
    let mut bytes = [0_u8; 32];
    rand::rng().fill_bytes(&mut bytes);
    URL_SAFE_NO_PAD.encode(bytes)
}

fn read_ready(stdout: impl std::io::Read) -> Result<ReadyMessage, HostError> {
    let deadline = Instant::now() + Duration::from_secs(15);
    let mut reader = BufReader::new(stdout);
    let mut line = String::new();
    while Instant::now() < deadline {
        line.clear();
        if reader.read_line(&mut line).map_err(|_| HostError::Start)? == 0 {
            return Err(HostError::Start);
        }
        let ready: ReadyMessage = serde_json::from_str(line.trim()).map_err(|_| HostError::InvalidReady)?;
        if ready.event == "ready" && ready.port > 0 {
            return Ok(ready);
        }
    }
    Err(HostError::Timeout)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn session_tokens_are_unique_and_url_safe() {
        let first = generate_session_token();
        let second = generate_session_token();
        assert_ne!(first, second);
        assert_eq!(first.len(), 43);
    }

    #[test]
    fn accepts_only_well_formed_ready_message() {
        let ready = read_ready("{\"event\":\"ready\",\"port\":43123}\n".as_bytes()).unwrap();
        assert_eq!(ready.port, 43123);
        assert!(read_ready("{\"event\":\"ready\",\"port\":0}\n".as_bytes()).is_err());
    }

    #[test]
    fn controlled_renderer_origin_is_limited_to_loopback_http() {
        assert!(is_loopback_renderer_origin("http://127.0.0.1:14202"));
        assert!(!is_loopback_renderer_origin("https://127.0.0.1:14202"));
        assert!(!is_loopback_renderer_origin("http://localhost:14202"));
        assert!(!is_loopback_renderer_origin("http://127.0.0.1:0"));
        assert!(!is_loopback_renderer_origin("http://example.test:14202"));
    }

    #[cfg(windows)]
    #[test]
    fn kill_on_close_job_reaps_a_process_tree() {
        let mut child = Command::new("cmd.exe")
            .args(["/C", "ping.exe", "-t", "127.0.0.1"])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .unwrap();
        let job = KillOnCloseJob::assign(&child).unwrap();

        drop(job);
        let deadline = Instant::now() + Duration::from_secs(5);
        while Instant::now() < deadline {
            if child.try_wait().unwrap().is_some() {
                return;
            }
            std::thread::sleep(Duration::from_millis(25));
        }
        let _ = child.kill();
        panic!("Closing the Windows job did not terminate the sidecar process tree.");
    }
}
