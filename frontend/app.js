const DEFAULT_BACKEND = "http://localhost:8000";
const LS_PROFILE = "vf_profile_v1";
const LS_LIBRARY = "vf_library_v1";
const LS_BACKEND = "vf_backend_v1";

const el = (id) => document.getElementById(id);

const views = {
  scan: el("view-scan"),
  profile: el("view-profile"),
  library: el("view-library"),
};

function showView(name) {
  Object.entries(views).forEach(([k, v]) => v.classList.toggle("hidden", k !== name));
  el("tab-scan").classList.toggle("active", name === "scan");
  el("tab-profile").classList.toggle("active", name === "profile");
  el("tab-library").classList.toggle("active", name === "library");
  if (name === "library") renderLibrary();
}

el("tab-scan").onclick = () => showView("scan");
el("tab-profile").onclick = () => showView("profile");
el("tab-library").onclick = () => showView("library");

function getBackend() {
  return localStorage.getItem(LS_BACKEND) || DEFAULT_BACKEND;
}

function setStatus(msg) { el("scan-status").textContent = msg || ""; }
function setProfileStatus(msg) { el("profile-status").textContent = msg || ""; }

function loadProfile() {
  try { return JSON.parse(localStorage.getItem(LS_PROFILE) || "{}"); }
  catch { return {}; }
}
function saveProfile(profile) { localStorage.setItem(LS_PROFILE, JSON.stringify(profile)); }

function parseCSV(text) {
  return (text || "").split(",").map(s => s.trim()).filter(Boolean);
}

function computeFit(profile, release) {
  const likedArtists = new Set((profile.artists || []).map(a => a.toLowerCase()));
  const likedGenres = new Set((profile.genres || []).map(g => g.toLowerCase()));
  const likedEras = new Set((profile.eras || []).map(e => e.toLowerCase()));

  let artistScore = 0;
  if (release.artist && likedArtists.has(release.artist.toLowerCase())) artistScore = 1;

  const relGenres = (release.genres || []).map(g => g.toLowerCase());
  let genreScore = 0;
  if (likedGenres.size && relGenres.length) {
    const inter = relGenres.filter(g => likedGenres.has(g)).length;
    const union = new Set([...relGenres, ...likedGenres]).size;
    genreScore = union ? inter / union : 0;
  }

  let eraScore = 0;
  if (release.decade && likedEras.size) {
    const d = release.decade.toLowerCase();
    eraScore = likedEras.has(d) ? 1 : 0;
  }

  const wArtist = 0.40, wGenre = 0.40, wEra = 0.20;
  const fit = (artistScore*wArtist) + (genreScore*wGenre) + (eraScore*wEra);
  const fitPct = Math.round(fit * 100);

  const why = [];
  if (artistScore) why.push("Artist matches your likes.");
  if (genreScore) why.push(`Genre overlap ${(genreScore*100).toFixed(0)}%.`);
  if (eraScore) why.push("Era matches your preference.");
  if (!why.length) why.push("No direct artist/genre/era match found in your profile.");

  return { fitPct, why: why.join(" ") };
}

let stream = null;
let capturedBlob = null;

async function startCamera() {
  const video = el("video");
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: "environment" },
      audio: false
    });
    video.srcObject = stream;
    el("btn-capture").disabled = false;
    el("btn-start").disabled = true;
    setStatus("Camera enabled.");
  } catch (e) {
    setStatus("Camera permission denied or not available. Check browser settings and try again.");
  }
}

function captureFrame() {
  const video = el("video");
  const canvas = el("canvas");
  const w = video.videoWidth;
  const h = video.videoHeight;
  if (!w || !h) { setStatus("Camera not ready yet. Try again."); return; }

  const size = Math.min(w, h);
  const sx = Math.floor((w - size) / 2);
  const sy = Math.floor((h - size) / 2);

  canvas.width = 1200; canvas.height = 1200;
  const ctx = canvas.getContext("2d");
  ctx.drawImage(video, sx, sy, size, size, 0, 0, canvas.width, canvas.height);

  canvas.classList.remove("hidden");
  video.classList.add("hidden");

  el("btn-retake").disabled = false;
  el("btn-identify").disabled = false;
  el("btn-capture").disabled = true;
  setStatus("Captured. If glare/angle is bad, retake.");
}

function retake() {
  el("canvas").classList.add("hidden");
  el("video").classList.remove("hidden");
  capturedBlob = null;
  el("btn-capture").disabled = false;
  el("btn-identify").disabled = true;
  el("btn-retake").disabled = true;
  setStatus("Ready.");
}

async function canvasToBlob() {
  return new Promise((resolve) => el("canvas").toBlob((b) => resolve(b), "image/jpeg", 0.85));
}

function showCandidates(candidates) {
  const wrap = el("candidates");
  const list = el("candidate-list");
  list.innerHTML = "";

  candidates.forEach((c) => {
    const item = document.createElement("div");
    item.className = "item";

    const title = document.createElement("div");
    title.className = "item-title";
    title.textContent = `${c.artist} — ${c.album}`;

    const meta = document.createElement("div");
    meta.className = "item-meta";
    meta.textContent = `Confidence: ${(c.confidence*100).toFixed(0)}%`;

    const actions = document.createElement("div");
    actions.className = "item-actions";
    const btn = document.createElement("button");
    btn.className = "primary";
    btn.textContent = "Select";
    btn.onclick = () => confirmCandidate(c.artist, c.album);
    actions.appendChild(btn);

    item.appendChild(title);
    item.appendChild(meta);
    item.appendChild(actions);
    list.appendChild(item);
  });

  wrap.classList.toggle("hidden", candidates.length === 0);
  el("manual").classList.add("hidden");
}

function showManual() { el("manual").classList.remove("hidden"); }

async function identify() {
  const backend = getBackend();
  el("backend-url").textContent = backend;

  if (!capturedBlob) capturedBlob = await canvasToBlob();
  if (!capturedBlob) { setStatus("Failed to capture image."); return; }

  setStatus("Identifying…");

  const fd = new FormData();
  fd.append("file", capturedBlob, "cover.jpg");

  try {
    const res = await fetch(`${backend}/api/identify?debug=false`, { method: "POST", body: fd });
    if (!res.ok) { setStatus(`Identify failed: ${await res.text()}`); return; }
    const data = await res.json();
    const candidates = data.candidates || [];
    if (!candidates.length) {
      setStatus("No confident matches found. Use manual entry.");
      showCandidates([]);
      showManual();
      return;
    }
    setStatus("Select the correct match.");
    showCandidates(candidates);
  } catch (e) {
    setStatus("Network error calling backend. Confirm backend URL and CORS settings.");
  }
}

async function confirmCandidate(artist, album) {
  const backend = getBackend();
  setStatus("Resolving metadata…");

  try {
    const res = await fetch(`${backend}/api/resolve`, {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({artist, album})
    });
    if (!res.ok) { setStatus(`Resolve failed: ${await res.text()}`); return; }
    const release = await res.json();
    showResult(release);
    setStatus("");
  } catch (e) {
    setStatus("Network error during resolve.");
  }
}

function showResult(release) {
  el("result").classList.remove("hidden");
  el("result-title").textContent = `${release.artist} — ${release.album}`;

  const metaParts = [];
  if (release.year) metaParts.push(`Year: ${release.year}`);
  if (release.decade) metaParts.push(`Era: ${release.decade}`);
  if (release.genres && release.genres.length) metaParts.push(`Genres: ${release.genres.join(", ")}`);
  if (!metaParts.length) metaParts.push("Metadata: (MVP scaffold) not yet connected to a music database.");

  el("result-meta").textContent = metaParts.join(" • ");

  const profile = loadProfile();
  const fit = computeFit(profile, release);
  el("fit-score").textContent = `${fit.fitPct}%`;
  el("fit-why").textContent = fit.why;

  el("btn-save").onclick = () => saveToLibrary(release, fit.fitPct);
}

function loadLibrary() {
  try { return JSON.parse(localStorage.getItem(LS_LIBRARY) || "[]"); }
  catch { return []; }
}
function saveLibrary(items) { localStorage.setItem(LS_LIBRARY, JSON.stringify(items)); }

function saveToLibrary(release, fitPct) {
  const lib = loadLibrary();
  lib.unshift({ ...release, fitPct, savedAt: new Date().toISOString() });
  saveLibrary(lib);
  setStatus("Saved to library.");
}

function renderLibrary() {
  const list = el("library-list");
  list.innerHTML = "";
  const lib = loadLibrary();
  if (!lib.length) { list.innerHTML = `<div class="small">No saved items yet.</div>`; return; }
  lib.forEach((r) => {
    const item = document.createElement("div");
    item.className = "item";
    const title = document.createElement("div");
    title.className = "item-title";
    title.textContent = `${r.artist} — ${r.album}`;
    const meta = document.createElement("div");
    meta.className = "item-meta";
    meta.textContent = `Fit: ${r.fitPct}% • Saved: ${new Date(r.savedAt).toLocaleString()}`;
    item.appendChild(title);
    item.appendChild(meta);
    list.appendChild(item);
  });
}

el("btn-clear-library").onclick = () => { saveLibrary([]); renderLibrary(); };
el("btn-start").onclick = startCamera;
el("btn-capture").onclick = captureFrame;
el("btn-retake").onclick = retake;
el("btn-identify").onclick = identify;
el("btn-none").onclick = showManual;
el("btn-manual-confirm").onclick = () => {
  const artist = el("manual-artist").value.trim();
  const album = el("manual-album").value.trim();
  if (!artist || !album) { setStatus("Please enter both artist and album."); return; }
  confirmCandidate(artist, album);
};

el("btn-save-profile").onclick = () => {
  const profile = {
    artists: parseCSV(el("liked-artists").value),
    genres: parseCSV(el("liked-genres").value),
    eras: parseCSV(el("liked-eras").value),
  };
  saveProfile(profile);
  setProfileStatus("Saved.");
  setTimeout(() => setProfileStatus(""), 1200);
};

function init() {
  el("backend-url").textContent = getBackend();
  const profile = loadProfile();
  el("liked-artists").value = (profile.artists || []).join(", ");
  el("liked-genres").value = (profile.genres || []).join(", ");
  el("liked-eras").value = (profile.eras || []).join(", ");

  const params = new URLSearchParams(location.search);
  if (params.get("backend")) {
    localStorage.setItem(LS_BACKEND, params.get("backend"));
    el("backend-url").textContent = getBackend();
  }
}
init();
