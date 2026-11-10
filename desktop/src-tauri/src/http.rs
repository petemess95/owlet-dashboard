use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::time::Duration;

use serde_json::Value;

use crate::SERVER_ADDR;

const HTTP_TIMEOUT: Duration = Duration::from_millis(1500);

// ---------------------------------------------------------------------------
// Localhost HTTP. HTTP/1.0 + Connection: close keeps the response un-chunked,
// so a tiny std-only client is enough (no TLS, no extra crate). Desktop mode
// treats every 127.0.0.1 request as the local admin, so no credentials.
// ---------------------------------------------------------------------------

pub(crate) fn http_json(method: &str, path: &str, body: Option<&str>) -> Result<Value, String> {
    let addr = SocketAddr::from(([127, 0, 0, 1], SERVER_ADDR.1));
    let mut stream = TcpStream::connect_timeout(&addr, HTTP_TIMEOUT).map_err(|e| e.to_string())?;
    let _ = stream.set_read_timeout(Some(HTTP_TIMEOUT));
    let _ = stream.set_write_timeout(Some(HTTP_TIMEOUT));
    let body = body.unwrap_or("");
    let request = format!(
        "{method} {path} HTTP/1.0\r\nHost: 127.0.0.1:{port}\r\nUser-Agent: owlet-desktop\r\n\
         Content-Type: application/json\r\nContent-Length: {len}\r\nConnection: close\r\n\r\n{body}",
        port = SERVER_ADDR.1,
        len = body.len(),
    );
    stream.write_all(request.as_bytes()).map_err(|e| e.to_string())?;
    let mut raw = Vec::new();
    stream.read_to_end(&mut raw).map_err(|e| e.to_string())?;
    let text = String::from_utf8_lossy(&raw);
    let (head, payload) = text.split_once("\r\n\r\n").ok_or("malformed response")?;
    let status = head.split_whitespace().nth(1).unwrap_or("");
    if status != "200" {
        return Err(format!("HTTP {status}"));
    }
    serde_json::from_str(payload).map_err(|e| e.to_string())
}
