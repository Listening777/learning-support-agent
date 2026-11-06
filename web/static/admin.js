/* 管理后台公共 JS：鉴权守卫 + 侧边栏 + 请求封装 */
function adminToken() { return localStorage.getItem("token"); }

// 鉴权守卫：无 token 或非管理员 → 跳登录页
async function adminGuard() {
  if (!adminToken()) { location.href = "/static/login.html"; return false; }
  const r = await fetch("/api/auth/me",
    { headers: { Authorization: "Bearer " + adminToken() } }).then(x => x.json()).catch(() => null);
  if (!r || r.code !== 0 || r.role !== "admin") {
    localStorage.removeItem("token"); location.href = "/static/login.html"; return false;
  }
  document.getElementById("adminName").textContent = r.name + "（管理员）";
  return true;
}

// 侧边栏注入（所有管理页共用）
function renderNav(active) {
  document.body.insertAdjacentHTML("afterbegin", `
  <div class="sidebar"><h1>学习支持助手 · 后台</h1>
    <div class="nav">
      <a href="/admin/dashboard" class="${active==='dashboard'?'active':''}">仪表盘</a>
      <a href="/admin/kb" class="${active==='kb'?'active':''}">知识库管理</a>
      <a href="/admin/prompt" class="${active==='prompt'?'active':''}">提示词模板</a>
      <a href="/admin/audit" class="${active==='audit'?'active':''}">对话审计</a>
      <a href="/chat">← 返回学生端</a>
    </div>
    <div style="padding:14px 18px;font-size:12px;border-top:1px solid #34465c;">
      <span id="adminName"></span><br>
      <a href="#" onclick="localStorage.removeItem('token');location.href='/static/login.html'"
         style="color:#8aa3c2;font-size:12px;">退出登录</a>
    </div>
  </div>`);
}

// 统一请求封装：非 0 code 弹中文错误
async function api(url, options = {}) {
  const resp = await fetch(url, {
    ...options,
    headers: { Authorization: "Bearer " + adminToken(), ...(options.headers || {}) }
  });
  const data = await resp.json();
  if (data.code !== 0) { toast(`错误 ${data.code}：${data.message}`); throw new Error(data.message); }
  return data;
}

function toast(msg) {
  const t = document.createElement("div");
  t.className = "toast"; t.textContent = msg; t.style.display = "block";
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 3000);
}
