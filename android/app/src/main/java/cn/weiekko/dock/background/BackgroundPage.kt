package cn.weiekko.dock.background

internal const val BACKGROUND_PAGE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>岸亭背景</title>
  <style>
    :root { color-scheme: dark; --bg:#0e141b; --card:#17202b; --line:#2a3848; --text:#e7eef6; --muted:#93a4b8; --accent:#1d4ed8; --bad:#fca5a5; }
    * { box-sizing: border-box; }
    body { margin:0; font:16px/1.5 "Segoe UI","PingFang SC",system-ui,sans-serif; background:var(--bg); color:var(--text); }
    main { max-width:880px; margin:0 auto; padding:28px 20px 48px; }
    h1 { font-size:28px; margin:0 0 6px; }
    p.lead { color:var(--muted); margin:0 0 22px; }
    form, .panel { background:var(--card); border:1px solid var(--line); border-radius:16px; padding:18px; }
    label { display:block; color:var(--muted); margin-bottom:8px; }
    input[type=password], input[type=text], input[type=file] { width:100%; }
    input[type=password], input[type=text] { padding:12px 14px; border-radius:10px; border:1px solid var(--line); background:#0d1218; color:var(--text); }
    .rules { color:var(--muted); font-size:13px; margin:0 0 14px; }
    .row { display:flex; gap:10px; flex-wrap:wrap; margin-top:14px; }
    button { cursor:pointer; border:0; border-radius:10px; padding:10px 14px; font-weight:600; background:#243044; color:var(--text); }
    button.primary { background:var(--accent); color:white; }
    button.danger { background:#7f1d1d; color:#fecaca; }
    button:disabled { opacity:.5; cursor:default; }
    .err { color:var(--bad); min-height:1.4em; margin:10px 0 0; }
    .grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(240px,1fr)); gap:14px; margin-top:16px; }
    article { background:var(--card); border:1px solid var(--line); border-radius:14px; overflow:hidden; }
    article.current { border-color:#60a5fa; }
    .preview { height:140px; background:#0b1016; display:flex; align-items:center; justify-content:center; }
    img, video { width:100%; height:140px; object-fit:cover; background:#000; }
    .meta { padding:12px; }
    .name { font-weight:650; word-break:break-all; }
    .sub { color:var(--muted); font-size:13px; margin-top:4px; }
    .badge { display:inline-block; margin-top:8px; padding:2px 8px; border-radius:999px; background:#143522; color:#86efac; font-size:12px; }
  </style>
</head>
<body>
<main>
  <h1>岸亭背景</h1>
  <p class="lead">上传图片或视频。口令用岸亭设置里的 Hub Token。新文件会进入索引，并立刻成为主屏背景。</p>
  <form id="login">
    <label for="token">Hub Token</label>
    <input id="token" type="password" autocomplete="current-password" />
    <div class="row"><button class="primary" type="submit">进入</button></div>
    <p class="err" id="loginErr"></p>
  </form>
  <section id="app" class="panel" hidden>
    <p class="rules">图片只接受 jpg、png、webp，不超过 20MB。视频只接受 mp4，不超过 200MB，时长 1 秒到 3 分钟，必须能播出画面。上传前会核验，并要填写实名。</p>
    <label for="realname">实名</label>
    <input id="realname" type="text" maxlength="20" autocomplete="name" placeholder="上传人的姓名" />
    <label for="file" style="margin-top:14px">上传图片或视频</label>
    <input id="file" type="file" accept="image/jpeg,image/png,image/webp,video/mp4" />
    <p class="sub" id="check">选择文件后会先核验。</p>
    <div class="row">
      <button class="primary" id="upload" type="button" disabled>上传并设为背景</button>
      <button id="clear" type="button">恢复纯色</button>
      <button id="reload" type="button">刷新索引</button>
    </div>
    <p class="err" id="appErr"></p>
    <div class="grid" id="list"></div>
  </section>
</main>
<script>
const login = document.querySelector("#login");
const app = document.querySelector("#app");
const loginErr = document.querySelector("#loginErr");
const appErr = document.querySelector("#appErr");
const list = document.querySelector("#list");

function size(bytes) {
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1048576) return (bytes / 1024).toFixed(1) + " KB";
  return (bytes / 1048576).toFixed(1) + " MB";
}

async function api(path, options) {
  const response = await fetch(path, options);
  const text = await response.text();
  let body = null;
  if (text) { try { body = JSON.parse(text); } catch (e) { body = { error: text }; } }
  if (!response.ok) throw new Error((body && body.error) || "请求失败");
  return body;
}

async function refresh() {
  appErr.textContent = "";
  const data = await api("/api/backgrounds");
  list.innerHTML = "";
  if (!data.items.length) {
    list.innerHTML = "<p class='sub'>索引是空的。</p>";
    return;
  }
  for (const item of data.items) {
    const card = document.createElement("article");
    if (item.id === data.current) card.className = "current";
    const media = item.kind === "image"
      ? '<img alt="" src="/api/backgrounds/' + encodeURIComponent(item.id) + '/media" />'
      : '<video muted loop autoplay playsinline src="/api/backgrounds/' + encodeURIComponent(item.id) + '/media"></video>';
    card.innerHTML = '<div class="preview">' + media + '</div><div class="meta"><div class="name"></div><div class="sub"></div></div>';
    card.querySelector(".name").textContent = item.name;
    card.querySelector(".sub").textContent = (item.owner || "未实名") + " · " + (item.kind === "image" ? "图片" : "视频") + " · " + size(item.bytes);
    const actions = document.createElement("div");
    actions.className = "row";
    if (item.id === data.current) {
      const badge = document.createElement("span");
      badge.className = "badge";
      badge.textContent = "当前背景";
      actions.appendChild(badge);
    } else {
      const use = document.createElement("button");
      use.textContent = "设为背景";
      use.onclick = async () => {
        await api("/api/backgrounds/" + encodeURIComponent(item.id) + "/select", { method: "POST" });
        await refresh();
      };
      actions.appendChild(use);
    }
    const remove = document.createElement("button");
    remove.className = "danger";
    remove.textContent = "删除";
    remove.onclick = async () => {
      await api("/api/backgrounds/" + encodeURIComponent(item.id), { method: "DELETE" });
      await refresh();
    };
    actions.appendChild(remove);
    card.querySelector(".meta").appendChild(actions);
    list.appendChild(card);
  }
}

login.onsubmit = async (event) => {
  event.preventDefault();
  loginErr.textContent = "";
  try {
    await api("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: document.querySelector("#token").value })
    });
    login.hidden = true;
    app.hidden = false;
    await refresh();
  } catch (error) {
    loginErr.textContent = error.message;
  }
};

const fileInput = document.querySelector("#file");
const uploadButton = document.querySelector("#upload");
const check = document.querySelector("#check");
let verifiedFile = null;

function realNameError(value) {
  const name = value.trim().replace(/\s+/g, "");
  const letters = Array.from(name).filter((ch) => /\p{L}/u.test(ch)).length;
  if (letters < 2 || name.length > 20) return "实名需为 2 到 20 个字";
  if (/\d/.test(name)) return "实名不能包含数字";
  return "";
}

function verifyFile(file) {
  const lower = file.name.toLowerCase();
  const image = /\.(jpg|jpeg|png|webp)$/.test(lower);
  const video = /\.mp4$/.test(lower);
  if (!image && !video) return Promise.reject(new Error("只接受 jpg、png、webp 或 mp4"));
  if (image && file.size > 20 * 1048576) return Promise.reject(new Error("图片不能超过 20MB"));
  if (video && file.size > 200 * 1048576) return Promise.reject(new Error("视频不能超过 200MB"));
  const url = URL.createObjectURL(file);
  if (image) {
    return new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => {
        URL.revokeObjectURL(url);
        if (!img.naturalWidth) reject(new Error("图片无法打开"));
        else resolve("已核验图片 " + img.naturalWidth + "×" + img.naturalHeight);
      };
      img.onerror = () => { URL.revokeObjectURL(url); reject(new Error("图片无法打开")); };
      img.src = url;
    });
  }
  return new Promise((resolve, reject) => {
    const probe = document.createElement("video");
    probe.preload = "metadata";
    probe.onloadedmetadata = () => {
      URL.revokeObjectURL(url);
      const duration = probe.duration;
      if (!probe.videoWidth || !isFinite(duration)) reject(new Error("视频没有画面"));
      else if (duration < 1 || duration > 180) reject(new Error("视频时长需在 1 秒到 3 分钟之间"));
      else resolve("已核验视频 " + probe.videoWidth + "×" + probe.videoHeight + " · " + duration.toFixed(1) + " 秒");
    };
    probe.onerror = () => { URL.revokeObjectURL(url); reject(new Error("视频无法播放，核验未通过")); };
    probe.src = url;
  });
}

fileInput.onchange = async () => {
  verifiedFile = null;
  uploadButton.disabled = true;
  const file = fileInput.files[0];
  if (!file) { check.textContent = "选择文件后会先核验。"; return; }
  check.textContent = "正在核验…";
  try {
    check.textContent = await verifyFile(file);
    verifiedFile = file;
    uploadButton.disabled = false;
  } catch (error) {
    check.textContent = error.message;
  }
};

uploadButton.onclick = async () => {
  const file = verifiedFile;
  appErr.textContent = "";
  const nameError = realNameError(document.querySelector("#realname").value);
  if (nameError) { appErr.textContent = nameError; return; }
  if (!file) { appErr.textContent = "先选择并通过核验"; return; }
  const body = new FormData();
  body.append("realname", document.querySelector("#realname").value.trim());
  body.append("file", file, file.name);
  uploadButton.disabled = true;
  try {
    await api("/api/backgrounds", { method: "POST", body });
    fileInput.value = "";
    verifiedFile = null;
    check.textContent = "选择文件后会先核验。";
    await refresh();
  } catch (error) {
    appErr.textContent = error.message;
    uploadButton.disabled = verifiedFile == null;
  }
};

document.querySelector("#clear").onclick = async () => {
  appErr.textContent = "";
  try { await api("/api/backgrounds/clear", { method: "POST" }); await refresh(); }
  catch (error) { appErr.textContent = error.message; }
};

document.querySelector("#reload").onclick = () => refresh().catch((error) => { appErr.textContent = error.message; });

refresh().then(() => { login.hidden = true; app.hidden = false; }).catch(() => {});
</script>
</body>
</html>
"""
