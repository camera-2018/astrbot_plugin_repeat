import React, { useCallback, useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  ChevronDown,
  Database,
  List,
  Loader2,
  MessageSquareText,
  Pencil,
  Plus,
  RefreshCw,
  RotateCcw,
  Save,
  Search,
  Trash2,
} from "lucide-react";

const h = React.createElement;
const realBridge = window.AstrBotPluginPage;
const bridge = realBridge || makeDevBridge();
const isDevBridge = !realBridge;

function cn(...parts) {
  return parts.filter(Boolean).join(" ");
}

function unwrap(res) {
  if (res && typeof res === "object" && "status" in res && "data" in res) {
    if (res.status && res.status !== "ok") {
      throw new Error(res.message || res.error || "请求失败");
    }
    return res.data;
  }
  return res;
}

function modeName(mode) {
  if (!mode) return "全部";
  return mode === "cont" ? "接话" : "复读";
}

function formatTime(ts) {
  if (!ts) return "";
  const value = Number(ts);
  if (!Number.isFinite(value)) return "";
  const ms = value > 1e12 ? value : value * 1000;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(ms));
}

function numberValue(value, fallback, min, max) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.min(max, Math.max(min, Math.trunc(parsed)));
}

async function waitBridgeReady() {
  if (typeof bridge.ready === "function") return bridge.ready();
  if (bridge.ready) return bridge.ready;
  return undefined;
}

const buttonBase =
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-zinc-300 disabled:pointer-events-none disabled:opacity-50";
const buttonVariants = {
  default: "bg-zinc-50 text-zinc-950 shadow hover:bg-zinc-200",
  destructive: "bg-red-600 text-zinc-50 shadow-sm hover:bg-red-600/90",
  outline:
    "border border-zinc-800 bg-zinc-950 shadow-sm hover:bg-zinc-800 hover:text-zinc-50",
  secondary: "bg-zinc-800 text-zinc-50 shadow-sm hover:bg-zinc-800/80",
  ghost: "hover:bg-zinc-800 hover:text-zinc-50",
  "ghost-danger": "text-red-400 hover:bg-red-950 hover:text-red-300",
};
const buttonSizes = {
  default: "h-9 px-4 py-2",
  sm: "h-8 rounded-md px-3 text-xs",
  icon: "h-8 w-8",
};

function Button({
  children,
  className,
  busy = false,
  disabled = false,
  size = "default",
  variant = "default",
  ...props
}) {
  return h(
    "button",
    {
      ...props,
      disabled: disabled || busy,
      className: cn(
        buttonBase,
        buttonVariants[variant] || buttonVariants.default,
        buttonSizes[size] || buttonSizes.default,
        className
      ),
    },
    busy ? h(Loader2, { size: 16, className: "animate-spin", "aria-hidden": true }) : null,
    ...React.Children.toArray(children)
  );
}

function Card({ children, className }) {
  return h(
    "section",
    { className: cn("rounded-lg border border-zinc-800 bg-zinc-950 text-zinc-50 shadow-sm", className) },
    children
  );
}

function Field({ children, className, label }) {
  return h(
    "label",
    { className: cn("grid gap-2 text-sm", className) },
    h("span", { className: "text-xs font-medium text-zinc-400" }, label),
    ...React.Children.toArray(children)
  );
}

function Input(props) {
  return h("input", {
    ...props,
    className: cn(
      "flex h-9 w-full rounded-md border border-zinc-800 bg-transparent px-3 py-1 text-sm shadow-sm transition-colors placeholder:text-zinc-500 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-zinc-300 disabled:cursor-not-allowed disabled:opacity-50",
      props.className
    ),
  });
}

function GroupPicker({ groups, onChange, onEnter, value }) {
  const [open, setOpen] = useState(false);

  return h(
    "div",
    { className: "grid gap-2 text-sm" },
    h("span", { className: "text-xs font-medium text-zinc-400" }, "群组 ID"),
    h(
      "div",
      { className: "relative" },
      h(Input, {
        "data-testid": "group-input",
        value,
        placeholder: "输入或选择群组",
        onFocus: () => setOpen(true),
        onClick: () => setOpen(true),
        onChange: (event) => {
          onChange(event.target.value);
          setOpen(true);
        },
        onKeyDown: (event) => {
          if (event.key === "Enter") onEnter?.();
          if (event.key === "Escape") setOpen(false);
        },
        onBlur: () => setTimeout(() => setOpen(false), 120),
        className: "pr-10",
        role: "combobox",
        "aria-expanded": open,
        "aria-controls": "group-picker-list",
      }),
      h(
        "button",
        {
          type: "button",
          "data-testid": "group-dropdown-button",
          className:
            "absolute right-1 top-1 inline-flex h-7 w-7 items-center justify-center rounded-md text-zinc-400 transition-colors hover:bg-zinc-800 hover:text-zinc-50",
          onMouseDown: (event) => event.preventDefault(),
          onClick: () => setOpen((current) => !current),
          "aria-label": "展开群组列表",
        },
        h(ChevronDown, { size: 15, "aria-hidden": true })
      ),
      open
        ? h(
            "div",
            {
              id: "group-picker-list",
              className:
                "absolute z-50 mt-1 max-h-60 w-full overflow-auto rounded-md border border-zinc-800 bg-zinc-950 p-1 text-sm shadow-lg",
              role: "listbox",
              "data-testid": "group-dropdown",
            },
            groups.length
              ? groups.map((item) =>
                  h(
                    "button",
                    {
                      key: item.value,
                      type: "button",
                      className: cn(
                        "flex w-full items-center justify-between rounded-sm px-2 py-1.5 text-left text-zinc-300 hover:bg-zinc-800 hover:text-zinc-50",
                        value === item.value && "bg-zinc-800 text-zinc-50"
                      ),
                      onMouseDown: (event) => event.preventDefault(),
                      onClick: () => {
                        onChange(item.value);
                        setOpen(false);
                      },
                      role: "option",
                      "aria-selected": value === item.value,
                    },
                    h("span", { className: "truncate" }, item.value),
                    h("span", { className: "ml-3 shrink-0 text-xs text-zinc-500" }, `${item.count} 条`)
                  )
                )
              : h("div", { className: "px-2 py-1.5 text-zinc-500" }, "暂无群组")
          )
        : null
    )
  );
}

function Textarea(props) {
  return h("textarea", {
    ...props,
    className: cn(
      "flex min-h-[120px] w-full rounded-md border border-zinc-800 bg-transparent px-3 py-2 text-sm shadow-sm placeholder:text-zinc-500 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-zinc-300 disabled:cursor-not-allowed disabled:opacity-50",
      props.className
    ),
  });
}

function Segmented({ label, onChange, options, value }) {
  return h(
    "div",
    {
      className:
        "inline-flex h-9 items-center justify-center rounded-lg bg-zinc-900 p-1 text-zinc-400",
      role: "radiogroup",
      "aria-label": label,
    },
    options.map((option) =>
      h(
        "button",
        {
          key: option.value,
          type: "button",
          "data-testid": option.testId,
          "data-state": value === option.value ? "active" : "inactive",
          className:
            "inline-flex h-7 min-w-16 items-center justify-center rounded-md px-3 text-sm font-medium transition-all hover:text-zinc-50 data-[state=active]:bg-zinc-950 data-[state=active]:text-zinc-50 data-[state=active]:shadow",
          onClick: () => onChange(option.value),
        },
        option.label
      )
    )
  );
}

function Tabs({ items, onChange, value }) {
  return h(
    "div",
    { className: "grid grid-cols-2 border-b border-zinc-800 bg-zinc-900 p-1", role: "tablist" },
    items.map((item) =>
      h(
        "button",
        {
          key: item.value,
          type: "button",
          role: "tab",
          "data-testid": item.testId,
          "aria-selected": value === item.value,
          "data-state": value === item.value ? "active" : "inactive",
          className:
            "inline-flex h-9 items-center justify-center gap-2 rounded-md px-3 text-sm font-medium text-zinc-400 transition-all hover:text-zinc-50 data-[state=active]:bg-zinc-950 data-[state=active]:text-zinc-50 data-[state=active]:shadow",
          onClick: () => onChange(item.value),
        },
        h(item.icon, { size: 16, "aria-hidden": true }),
        item.label
      )
    )
  );
}

function Badge({ children, tone = "neutral" }) {
  const tones = {
    neutral: "border-transparent bg-zinc-800 text-zinc-50",
    primary: "border-transparent bg-zinc-50 text-zinc-950",
    success: "border-emerald-900 bg-emerald-950 text-emerald-300",
    destructive: "border-red-900 bg-red-950 text-red-300",
  };
  return h(
    "span",
    {
      className: cn(
        "inline-flex items-center rounded-md border px-2.5 py-0.5 text-xs font-semibold transition-colors",
        tones[tone] || tones.neutral
      ),
    },
    children
  );
}

function Stat({ icon, label, value }) {
  return h(
    Card,
    { className: "grid min-h-20 grid-cols-[2rem_1fr] items-center gap-x-3 p-4" },
    h("div", { className: "row-span-2 grid h-8 w-8 place-items-center rounded-md border border-zinc-800 bg-zinc-900 text-zinc-400" }, h(icon, { size: 16, "aria-hidden": true })),
    h("span", { className: "text-xs text-zinc-400" }, label),
    h("strong", { className: "text-lg font-semibold leading-none" }, value)
  );
}

function Message({ message }) {
  const tones = {
    ok: "text-emerald-400",
    warn: "text-yellow-400",
    err: "text-red-400",
  };
  return h(
    "span",
    {
      className: cn("flex min-h-9 items-center text-sm text-zinc-400", tones[message.kind]),
      role: "status",
      "aria-live": "polite",
    },
    message.text
  );
}

function EmptyState({ icon = Database, text }) {
  return h(
    "div",
    {
      className:
        "grid min-h-36 place-items-center gap-2 rounded-md border border-dashed border-zinc-800 bg-zinc-900/40 p-8 text-center text-sm text-zinc-500",
    },
    h(icon, { size: 24, "aria-hidden": true }),
    h("span", null, text)
  );
}

function MemoryItem({ item, onDelete, onEdit, showScore = false }) {
  const meta = [
    item.id ? `ID ${item.id}` : "",
    item.sender_id ? `来源 ${item.sender_id}` : "",
    formatTime(item.ts),
  ].filter(Boolean);

  return h(
    "article",
    {
      className: "grid gap-3 rounded-lg border border-zinc-800 bg-zinc-950 p-4",
      "data-id": item.id || "",
    },
    h(
      "div",
      { className: "flex items-start justify-between gap-3" },
      h(
        "div",
        { className: "flex flex-wrap gap-2" },
        h(Badge, { tone: item.mode === "cont" ? "success" : "primary" }, modeName(item.mode)),
        showScore && typeof item.score === "number"
          ? h(Badge, { tone: "neutral" }, `相似度 ${item.score.toFixed(3)}`)
          : null
      ),
      h(
        "div",
        { className: "flex shrink-0 gap-1" },
        h(
          Button,
          { variant: "ghost", size: "icon", type: "button", title: "编辑", onClick: () => onEdit(item) },
          h(Pencil, { size: 15, "aria-hidden": true })
        ),
        h(
          Button,
          { variant: "ghost-danger", size: "icon", type: "button", title: "删除", onClick: () => onDelete(item.id) },
          h(Trash2, { size: 15, "aria-hidden": true })
        )
      )
    ),
    h("div", { className: "whitespace-pre-wrap break-words text-sm leading-6 text-zinc-50" }, item.text || ""),
    item.response
      ? h("div", { className: "border-l-2 border-zinc-700 pl-3 text-sm leading-6 text-zinc-400" }, item.response)
      : null,
    h("div", { className: "flex flex-wrap gap-2 text-xs text-zinc-500" }, meta.map((part) => h("span", { key: part }, part)))
  );
}

function ResultList({ emptyIcon, emptyText, items, onDelete, onEdit, showScore = false }) {
  if (!items.length) return h(EmptyState, { icon: emptyIcon, text: emptyText });
  return h(
    "div",
    { className: "grid gap-3" },
    items.map((item) =>
      h(MemoryItem, {
        key: item.id,
        item,
        onDelete,
        onEdit,
        showScore,
      })
    )
  );
}

function App() {
  const [ready, setReady] = useState(false);
  const [message, setMessage] = useState({ text: "", kind: "" });
  const [busy, setBusy] = useState("");
  const [stats, setStats] = useState({ total: 0, by_mode: [], by_group: [] });
  const [group, setGroup] = useState("");
  const [activeTab, setActiveTab] = useState("search");

  const [searchMode, setSearchMode] = useState("echo");
  const [searchQuery, setSearchQuery] = useState("");
  const [searchLimit, setSearchLimit] = useState(5);
  const [searchResults, setSearchResults] = useState([]);

  const [listMode, setListMode] = useState("");
  const [listLimit, setListLimit] = useState(20);
  const [listItems, setListItems] = useState([]);
  const [listNext, setListNext] = useState(null);

  const [form, setForm] = useState({ id: "", mode: "echo", text: "", response: "" });

  const groups = useMemo(
    () => [...(stats.by_group || [])].sort((a, b) => (b.count || 0) - (a.count || 0)),
    [stats.by_group]
  );
  const modeCounts = useMemo(
    () => Object.fromEntries((stats.by_mode || []).map((item) => [item.value, item.count])),
    [stats.by_mode]
  );

  const flash = useCallback((text, kind = "") => {
    setMessage({ text, kind });
  }, []);

  const loadStats = useCallback(async () => {
    try {
      const data = unwrap(await bridge.apiGet("stats")) || {};
      setStats({
        total: data.total || 0,
        by_mode: Array.isArray(data.by_mode) ? data.by_mode : [],
        by_group: Array.isArray(data.by_group) ? data.by_group : [],
      });
      return data;
    } catch (e) {
      flash("统计加载失败: " + e.message, "err");
      return null;
    }
  }, [flash]);

  const refreshStats = useCallback(async () => {
    setBusy("stats");
    try {
      await loadStats();
      flash("统计已刷新", "ok");
    } finally {
      setBusy("");
    }
  }, [flash, loadStats]);

  useEffect(() => {
    let mounted = true;
    waitBridgeReady()
      .catch(() => undefined)
      .then(async () => {
        if (!mounted) return;
        setReady(true);
        await loadStats();
        if (isDevBridge) flash("本地预览模式: 使用模拟数据", "warn");
      });
    return () => {
      mounted = false;
    };
  }, [flash, loadStats]);

  useEffect(() => {
    if (!group && groups.length) setGroup(groups[0].value);
  }, [group, groups]);

  const resetForm = useCallback((mode = form.mode || "echo") => {
    setForm({ id: "", mode, text: "", response: "" });
  }, [form.mode]);

  const onGroupChange = useCallback((value) => {
    setGroup(value);
    setSearchResults([]);
    setListItems([]);
    setListNext(null);
  }, []);

  const loadList = useCallback(
    async (reset = true) => {
      const currentGroup = group.trim();
      if (!currentGroup) {
        flash("请先填群组 ID", "err");
        return;
      }
      setBusy(reset ? "list" : "more");
      try {
        const params = {
          group: currentGroup,
          mode: listMode,
          limit: numberValue(listLimit, 20, 1, 100),
        };
        if (!reset && listNext) params.offset = listNext;
        const data = unwrap(await bridge.apiGet("list", params)) || {};
        const items = Array.isArray(data.items) ? data.items : [];
        setListItems((prev) => (reset ? items : [...prev, ...items]));
        setListNext(data.next || null);
        flash(`已加载 ${items.length} 条${data.next ? ", 还有更多" : ""}`, "ok");
      } catch (e) {
        flash("加载失败: " + e.message, "err");
      } finally {
        setBusy("");
      }
    },
    [flash, group, listLimit, listMode, listNext]
  );

  const doSearch = useCallback(async () => {
    const currentGroup = group.trim();
    const query = searchQuery.trim();
    if (!currentGroup) {
      flash("请先填群组 ID", "err");
      return;
    }
    if (!query) {
      flash("请输入搜索内容", "err");
      return;
    }
    setBusy("search");
    try {
      const data = unwrap(
        await bridge.apiPost("search", {
          group: currentGroup,
          mode: searchMode,
          query,
          limit: numberValue(searchLimit, 10, 1, 50),
        })
      ) || [];
      const items = Array.isArray(data) ? data : [];
      setSearchResults(items);
      flash(`命中 ${items.length} 条`, "ok");
    } catch (e) {
      flash("搜索失败: " + e.message, "err");
    } finally {
      setBusy("");
    }
  }, [flash, group, searchLimit, searchMode, searchQuery]);

  const save = useCallback(async () => {
    const currentGroup = group.trim();
    const text = form.text.trim();
    const response = form.response.trim();
    if (!currentGroup) {
      flash("请先填群组 ID", "err");
      return;
    }
    if (!text) {
      flash("文本/触发词必填", "err");
      return;
    }
    if (form.mode === "cont" && !response) {
      flash("接话模式必须填写回复", "err");
      return;
    }
    setBusy("save");
    try {
      const body = { group: currentGroup, mode: form.mode, text, response };
      if (form.id) body.id = form.id;
      await bridge.apiPost("upsert", body);
      flash("已保存", "ok");
      resetForm(form.mode);
      await loadStats();
      setActiveTab("list");
      await loadList(true);
    } catch (e) {
      flash("保存失败: " + e.message, "err");
    } finally {
      setBusy("");
    }
  }, [flash, form, group, loadList, loadStats, resetForm]);

  const editItem = useCallback((item) => {
    setForm({
      id: item.id || "",
      mode: item.mode || "echo",
      text: item.text || "",
      response: item.response || "",
    });
  }, []);

  const deletePoint = useCallback(
    async (id) => {
      if (!id || !confirm("确认删除这条记忆?")) return;
      setBusy(`delete:${id}`);
      try {
        await bridge.apiPost("delete", { id });
        setSearchResults((prev) => prev.filter((item) => item.id !== id));
        setListItems((prev) => prev.filter((item) => item.id !== id));
        if (form.id === id) resetForm(form.mode);
        await loadStats();
        flash("已删除", "ok");
      } catch (e) {
        flash("删除失败: " + e.message, "err");
      } finally {
        setBusy("");
      }
    },
    [flash, form.id, form.mode, loadStats, resetForm]
  );

  const clearGroup = useCallback(async () => {
    const currentGroup = group.trim();
    if (!currentGroup) {
      flash("请先填群组 ID", "err");
      return;
    }
    if (!confirm(`确认清空群 ${currentGroup} 的所有记忆? 此操作不可撤销。`)) return;
    setBusy("clear");
    try {
      await bridge.apiPost("delete", { group: currentGroup, clear: true });
      setSearchResults([]);
      setListItems([]);
      setListNext(null);
      resetForm();
      await loadStats();
      flash("已清空当前群组", "ok");
    } catch (e) {
      flash("清空失败: " + e.message, "err");
    } finally {
      setBusy("");
    }
  }, [flash, group, loadStats, resetForm]);

  return h(
    "div",
    { className: "mx-auto grid min-h-screen w-full max-w-6xl gap-4 px-4 py-6 sm:px-6 lg:px-8" },
    h(
      "header",
      { className: "grid gap-4 lg:grid-cols-[1fr_520px] lg:items-end" },
      h(
        "div",
        { className: "flex min-w-0 items-center gap-3" },
        h("div", { className: "grid h-11 w-11 shrink-0 place-items-center rounded-lg border border-zinc-800 bg-zinc-950" }, h(MessageSquareText, { size: 22, "aria-hidden": true })),
        h(
          "div",
          { className: "min-w-0" },
          h("p", { className: "text-sm text-zinc-400" }, "AstrBot Plugin"),
          h("h1", { className: "truncate text-3xl font-semibold tracking-tight" }, "Repeat 语义复读")
        )
      ),
      h(
        "div",
        { className: "grid grid-cols-2 gap-3 sm:grid-cols-4" },
        h(Stat, { icon: Database, label: "总量", value: stats.total || 0 }),
        h(Stat, { icon: MessageSquareText, label: "复读", value: modeCounts.echo || 0 }),
        h(Stat, { icon: List, label: "接话", value: modeCounts.cont || 0 }),
        h(Stat, { icon: Database, label: "群组", value: groups.length })
      )
    ),
    h(
      Card,
      { className: "grid gap-3 p-4 lg:grid-cols-[minmax(260px,1fr)_auto_auto_minmax(180px,0.8fr)] lg:items-end" },
      h(GroupPicker, {
        groups,
        value: group,
        onChange: onGroupChange,
        onEnter: () => loadList(true),
      }),
      h(
        Button,
        { type: "button", variant: "outline", busy: busy === "stats", onClick: refreshStats, disabled: !ready, "data-testid": "refresh-button" },
        h(RefreshCw, { size: 16, "aria-hidden": true }),
        "刷新"
      ),
      h(
        Button,
        { type: "button", variant: "outline", className: "border-red-900 text-red-400 hover:bg-red-950 hover:text-red-300", busy: busy === "clear", onClick: clearGroup, disabled: !ready, "data-testid": "clear-group-button" },
        h(Trash2, { size: 16, "aria-hidden": true }),
        "清空群组"
      ),
      h(Message, { message })
    ),
    h(
      "main",
      { className: "grid gap-4 lg:grid-cols-[minmax(0,1.35fr)_minmax(330px,0.65fr)] lg:items-start" },
      h(
        Card,
        { className: "overflow-hidden" },
        h(Tabs, {
          value: activeTab,
          onChange: setActiveTab,
          items: [
            { value: "search", label: "搜索", icon: Search, testId: "tab-search" },
            { value: "list", label: "记忆", icon: List, testId: "tab-list" },
          ],
        }),
        activeTab === "search"
          ? h(
              "div",
              { className: "grid gap-4 p-4" },
              h(
                "div",
                { className: "flex flex-col gap-3 sm:flex-row sm:items-end" },
                h(Segmented, {
                  label: "搜索模式",
                  value: searchMode,
                  onChange: setSearchMode,
                  options: [
                    { value: "echo", label: "复读", testId: "search-mode-echo" },
                    { value: "cont", label: "接话", testId: "search-mode-cont" },
                  ],
                }),
                h(Field, { label: "查询", className: "flex-1" }, h(Input, {
                  "data-testid": "search-query",
                  value: searchQuery,
                  placeholder: "输入要匹配的文本",
                  onChange: (event) => setSearchQuery(event.target.value),
                  onKeyDown: (event) => {
                    if (event.key === "Enter") doSearch();
                  },
                })),
                h(Field, { label: "数量", className: "sm:w-24" }, h(Input, {
                  "data-testid": "search-limit",
                  type: "number",
                  min: 1,
                  max: 50,
                  value: searchLimit,
                  onChange: (event) => setSearchLimit(event.target.value),
                })),
                h(
                  Button,
                  { type: "button", busy: busy === "search", onClick: doSearch, disabled: !ready, "data-testid": "search-button" },
                  h(Search, { size: 16, "aria-hidden": true }),
                  "搜索"
                )
              ),
              h("div", { className: "justify-self-end text-xs text-zinc-500" }, `${searchResults.length} 条`),
              h(ResultList, {
                emptyIcon: Search,
                emptyText: "暂无搜索结果",
                items: searchResults,
                onDelete: deletePoint,
                onEdit: editItem,
                showScore: true,
              })
            )
          : h(
              "div",
              { className: "grid gap-4 p-4" },
              h(
                "div",
                { className: "flex flex-col gap-3 sm:flex-row sm:items-end" },
                h(Segmented, {
                  label: "列表模式",
                  value: listMode,
                  onChange: setListMode,
                  options: [
                    { value: "", label: "全部", testId: "list-mode-all" },
                    { value: "echo", label: "复读", testId: "list-mode-echo" },
                    { value: "cont", label: "接话", testId: "list-mode-cont" },
                  ],
                }),
                h(Field, { label: "数量", className: "sm:w-24" }, h(Input, {
                  "data-testid": "list-limit",
                  type: "number",
                  min: 1,
                  max: 100,
                  value: listLimit,
                  onChange: (event) => setListLimit(event.target.value),
                })),
                h(
                  Button,
                  { type: "button", busy: busy === "list", onClick: () => loadList(true), disabled: !ready, "data-testid": "list-button" },
                  h(List, { size: 16, "aria-hidden": true }),
                  "加载"
                ),
                listNext
                  ? h(
                      Button,
                      { type: "button", variant: "outline", busy: busy === "more", onClick: () => loadList(false), disabled: !ready, "data-testid": "list-more-button" },
                      "更多"
                    )
                  : null
              ),
              h("div", { className: "justify-self-end text-xs text-zinc-500" }, `${listItems.length} 条${listNext ? "+" : ""}`),
              h(ResultList, {
                emptyIcon: Database,
                emptyText: "暂无记忆",
                items: listItems,
                onDelete: deletePoint,
                onEdit: editItem,
              })
            )
      ),
      h(
        Card,
        { className: "lg:sticky lg:top-4" },
        h(
          "div",
          { className: "flex items-center justify-between gap-3 border-b border-zinc-800 p-4" },
          h("div", null, h("p", { className: "text-xs text-zinc-500" }, form.id ? "Edit" : "Create"), h("h2", { className: "text-lg font-semibold tracking-tight" }, form.id ? "编辑记忆" : "新增记忆")),
          h(Badge, { tone: form.mode === "cont" ? "success" : "primary" }, modeName(form.mode))
        ),
        h(
          "div",
          { className: "grid gap-4 p-4" },
          h(Segmented, {
            label: "表单模式",
            value: form.mode,
            onChange: (mode) => setForm((prev) => ({ ...prev, mode, response: mode === "echo" ? "" : prev.response })),
            options: [
              { value: "echo", label: "复读", testId: "form-mode-echo" },
              { value: "cont", label: "接话", testId: "form-mode-cont" },
            ],
          }),
          h(Field, { label: "文本" }, h(Textarea, {
            "data-testid": "form-text",
            rows: 8,
            value: form.text,
            placeholder: "复读文本或接话触发文本",
            onChange: (event) => setForm((prev) => ({ ...prev, text: event.target.value })),
          })),
          form.mode === "cont"
            ? h(Field, { label: "接话回复" }, h(Textarea, {
                "data-testid": "form-response",
                rows: 5,
                value: form.response,
                placeholder: "命中后发送的回复",
                onChange: (event) => setForm((prev) => ({ ...prev, response: event.target.value })),
              }))
            : null,
          h(
            "div",
            { className: "flex flex-col gap-2 sm:flex-row" },
            h(
              Button,
              { type: "button", busy: busy === "save", onClick: save, disabled: !ready, "data-testid": "save-button" },
              form.id ? h(Save, { size: 16, "aria-hidden": true }) : h(Plus, { size: 16, "aria-hidden": true }),
              form.id ? "保存修改" : "保存"
            ),
            h(
              Button,
              { type: "button", variant: "outline", onClick: () => resetForm(), disabled: busy === "save", "data-testid": "reset-button" },
              h(RotateCcw, { size: 16, "aria-hidden": true }),
              "重置"
            )
          )
        )
      )
    )
  );
}

function makeDevBridge() {
  let items = [
    {
      id: "dev-echo-1",
      group_id: "943393400",
      mode: "echo",
      text: "这个梗我昨天也见过, 真的很适合复读。",
      response: "",
      sender_id: "10001",
      ts: Math.floor(Date.now() / 1000) - 7200,
    },
    {
      id: "dev-cont-1",
      group_id: "943393400",
      mode: "cont",
      text: "今天晚上吃什么",
      response: "火锅吧, 这个天气正合适。",
      sender_id: "webui",
      ts: Math.floor(Date.now() / 1000) - 3600,
    },
    {
      id: "dev-echo-2",
      group_id: "1122334455",
      mode: "echo",
      text: "先别急, 让我再想三秒。",
      response: "",
      sender_id: "10002",
      ts: Math.floor(Date.now() / 1000) - 900,
    },
  ];

  const later = (value) => new Promise((resolve) => setTimeout(() => resolve(value), 120));
  const nextId = () =>
    crypto.randomUUID ? crypto.randomUUID() : `dev-${Date.now()}-${Math.random().toString(16).slice(2)}`;

  function stats() {
    const countBy = (key) =>
      Object.values(
        items.reduce((acc, item) => {
          const value = item[key] || "";
          acc[value] = acc[value] || { value, count: 0 };
          acc[value].count += 1;
          return acc;
        }, {})
      );
    return {
      total: items.length,
      by_mode: countBy("mode"),
      by_group: countBy("group_id"),
    };
  }

  function filtered({ group, mode }) {
    return items.filter((item) => item.group_id === group && (!mode || item.mode === mode));
  }

  function score(query, item) {
    const haystack = `${item.text} ${item.response || ""}`.toLowerCase();
    const needle = String(query || "").toLowerCase();
    if (haystack.includes(needle)) return 0.942;
    const tokens = [...new Set(needle.split(/\s+/).filter(Boolean))];
    if (!tokens.length) return 0;
    const matched = tokens.filter((token) => haystack.includes(token)).length;
    return matched ? 0.62 + matched / tokens.length / 4 : 0.31;
  }

  return {
    ready: async () => later(true),
    apiGet: async (path, params = {}) => {
      if (path === "stats") return later(stats());
      if (path === "groups") return later(stats().by_group.map((group) => group.value));
      if (path === "list") {
        const limit = Number(params.limit) || 20;
        const offset = Number(params.offset || 0);
        const all = filtered(params);
        const rows = all.slice(offset, offset + limit);
        const next = offset + limit < all.length ? String(offset + limit) : null;
        return later({ items: rows, next });
      }
      throw new Error(`未知接口: ${path}`);
    },
    apiPost: async (path, body = {}) => {
      if (path === "search") {
        const rows = filtered(body)
          .map((item) => ({ ...item, score: score(body.query, item) }))
          .sort((a, b) => b.score - a.score)
          .slice(0, Number(body.limit) || 10);
        return later(rows);
      }
      if (path === "upsert") {
        const row = {
          id: body.id || nextId(),
          group_id: body.group,
          mode: body.mode,
          text: body.text,
          response: body.response || "",
          sender_id: "webui",
          ts: Math.floor(Date.now() / 1000),
        };
        const index = items.findIndex((item) => item.id === row.id);
        if (index >= 0) items[index] = row;
        else items.unshift(row);
        return later({ id: row.id });
      }
      if (path === "delete") {
        if (body.id) {
          items = items.filter((item) => item.id !== body.id);
          return later({ deleted: body.id });
        }
        if (body.group && body.clear) {
          items = items.filter((item) => item.group_id !== body.group);
          return later({ cleared_group: body.group });
        }
      }
      throw new Error(`未知接口: ${path}`);
    },
  };
}

createRoot(document.getElementById("root")).render(h(App));
