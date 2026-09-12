// Thin wrapper around fetch for the Rangdhara JSON API.
// Every write carries the X-Requested-With header the server requires (CSRF guard).

export class ApiError extends Error {
  constructor(status, body) {
    const error = (body && body.error) || {};
    super(error.message || "Something went wrong. Please try again.");
    this.status = status;
    this.code = error.code || "error";
    this.fields = error.fields || {};
    this.data = error;
  }
}

function buildUrl(path, query) {
  const url = new URL(`/api/v1${path}`, location.origin);
  Object.entries(query || {}).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") url.searchParams.set(key, value);
  });
  return url;
}

export async function api(path, { method = "GET", body, query, signal } = {}) {
  const init = { method, headers: { Accept: "application/json" }, credentials: "same-origin", signal };
  if (method !== "GET") init.headers["X-Requested-With"] = "rangdhaara";
  if (body instanceof FormData) {
    init.body = body;
  } else if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(buildUrl(path, query), init);
  } catch (err) {
    if (err.name === "AbortError") throw err;
    throw new ApiError(0, { error: { code: "network", message: "Can't reach the studio right now. Check your connection and try again." } });
  }
  const data = await response.json().catch(() => null);
  if (!response.ok) throw new ApiError(response.status, data);
  return data;
}

// Multipart upload with progress, for large lesson videos.
export function upload(path, formData, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/api/v1${path}`);
    xhr.setRequestHeader("X-Requested-With", "rangdhaara");
    xhr.upload.onprogress = (e) => { if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total); };
    xhr.onload = () => {
      let data = null;
      try { data = JSON.parse(xhr.responseText); } catch { /* not JSON */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data);
      else reject(new ApiError(xhr.status, data));
    };
    xhr.onerror = () => reject(new ApiError(0, { error: { code: "network", message: "The upload was interrupted. Check your connection and try again." } }));
    xhr.send(formData);
  });
}
