const bridge = window.AstrBotPluginPage;

const $ = (id) => document.getElementById(id);
const esc = (s) =>
  String(s ?? "").replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])
  );

function flash(text, kind = "") {
  const el = $("msg");
  el.textContent = text || "";
  el.className = "msg" + (kind ? " " + kind : "");
}

function curGroup() {
  return $("group").value.trim();
}

// ---------- 渲染 ----------
function itemEl(it, { score } = {}) {
  const el = document.createElement("div");
  el.className = "item";
  const modeTag = `<span class="tag ${esc(it.mode)}">${it.mode === "cont" ? "顺延" : "附和"}</span>`;
  const scoreTag =
    typeof score === "number" ? `<span class="score">${score.toFixed(3)}</span>` : "";
  const resp = it.response
    ? `<div class="resp">↳ ${esc(it.response)}</div>`
    : "";
  el.innerHTML = `
    <div class="meta">${modeTag}${scoreTag}<span class="id">${esc(it.id)}</span></div>
    <div class="text">${esc(it.text)}</div>${resp}
    <div class="actions">
      <button class="mini ghost edit">编辑</button>
      <button class="mini del">删除</button>
    </div>`;
  el.querySelector(".edit").addEventListener("click", () => fillForm(it));
  el.querySelector(".del").addEventListener("click", () => delPoint(it.id));
  return el;
}

// ---------- 数据操作 ----------
async function loadStats() {
  try {
    const s = await bridge.apiGet("stats");
    const byMode = (s.by_mode || [])
      .map((m) => `${m.value === "cont" ? "顺延" : "附和"} ${m.count}`)
      .join(" / ");
    $("stats").innerHTML =
      `<span class="chip">总记忆点 ${s.total}</span>` +
      (byMode ? `<span class="chip">${esc(byMode)}</span>` : "") +
      `<span class="chip">群数 ${(s.by_group || []).length}</span>`;
    const dl = $("groupList");
    dl.innerHTML = (s.by_group || [])
      .map((g) => `<option value="${esc(g.value)}">${esc(g.value)} (${g.count})</option>`)
      .join("");
  } catch (e) {
    $("stats").textContent = "统计加载失败:" + e.message;
  }
}

async function doSearch() {
  const group = curGroup();
  if (!group) return flash("请先填群号", "err");
  const query = $("searchQuery").value.trim();
  if (!query) return flash("请输入搜索内容", "err");
  flash("搜索中…");
  try {
    const data = await bridge.apiPost("search", {
      group,
      mode: $("searchMode").value,
      query,
      limit: Number($("searchLimit").value) || 10,
    });
    const box = $("searchResult");
    box.innerHTML = "";
    if (!data.length) box.innerHTML = `<div class="empty">无匹配</div>`;
    for (const it of data) box.appendChild(itemEl(it, { score: it.score }));
    flash(`命中 ${data.length} 条`, "ok");
  } catch (e) {
    flash("搜索失败:" + e.message, "err");
  }
}

let listNext = null;
async function loadList(reset = true) {
  const group = curGroup();
  if (!group) return flash("请先填群号", "err");
  if (reset) listNext = null;
  flash("加载中…");
  try {
    const params = {
      group,
      mode: $("listMode").value,
      limit: Number($("listLimit").value) || 20,
    };
    if (!reset && listNext) params.offset = listNext;
    const data = await bridge.apiGet("list", params);
    const box = $("listResult");
    if (reset) box.innerHTML = "";
    if (reset && !data.items.length) box.innerHTML = `<div class="empty">没有数据</div>`;
    for (const it of data.items) box.appendChild(itemEl(it));
    listNext = data.next;
    $("listMore").disabled = !listNext;
    flash(`已加载 ${data.items.length} 条${listNext ? "(还有更多)" : ""}`, "ok");
  } catch (e) {
    flash("加载失败:" + e.message, "err");
  }
}

function fillForm(it) {
  $("editId").value = it.id || "";
  $("formMode").value = it.mode || "echo";
  $("formText").value = it.text || "";
  $("formResponse").value = it.response || "";
  toggleResponse();
  $("formTitle").textContent = it.id ? "编辑记忆点" : "新增记忆点";
  $("formText").focus();
  $("formText").scrollIntoView({ behavior: "smooth", block: "center" });
}

function resetForm() {
  fillForm({ mode: $("formMode").value });
  $("editId").value = "";
  $("formText").value = "";
  $("formResponse").value = "";
  $("formTitle").textContent = "新增记忆点";
}

function toggleResponse() {
  $("responseWrap").style.display = $("formMode").value === "cont" ? "block" : "none";
}

async function save() {
  const group = curGroup();
  if (!group) return flash("请先填群号", "err");
  const mode = $("formMode").value;
  const text = $("formText").value.trim();
  if (!text) return flash("文本/触发词必填", "err");
  const response = $("formResponse").value.trim();
  if (mode === "cont" && !response) return flash("顺延模式必须填接续句", "err");
  flash("保存中…");
  try {
    const body = { group, mode, text, response };
    const id = $("editId").value;
    if (id) body.id = id;
    await bridge.apiPost("upsert", body);
    flash("已保存", "ok");
    resetForm();
    await loadStats();
    await loadList(true);
  } catch (e) {
    flash("保存失败:" + e.message, "err");
  }
}

async function delPoint(id) {
  if (!confirm("确认删除这条记忆点?")) return;
  try {
    await bridge.apiPost("delete", { id });
    flash("已删除", "ok");
    await loadStats();
    // 从两个列表里就地移除已删项
    for (const box of [$("searchResult"), $("listResult")]) {
      [...box.querySelectorAll(".item")].forEach((el) => {
        if (el.querySelector(".id")?.textContent === id) el.remove();
      });
    }
  } catch (e) {
    flash("删除失败:" + e.message, "err");
  }
}

// ---------- 初始化 ----------
async function init() {
  try {
    await bridge.ready();
  } catch (_) {}
  $("refresh").addEventListener("click", loadStats);
  $("searchBtn").addEventListener("click", doSearch);
  $("searchQuery").addEventListener("keydown", (e) => e.key === "Enter" && doSearch());
  $("listBtn").addEventListener("click", () => loadList(true));
  $("listMore").addEventListener("click", () => loadList(false));
  $("formMode").addEventListener("change", toggleResponse);
  $("saveBtn").addEventListener("click", save);
  $("resetBtn").addEventListener("click", resetForm);
  toggleResponse();
  await loadStats();
}

init();
