/* Вкладки на карточке проекта */
document.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-tab]");
  if (!btn) return;
  const scope = btn.closest("[data-tabs]");
  scope.querySelectorAll("[data-tab]").forEach((b) => b.classList.toggle("active", b === btn));
  const id = btn.dataset.tab;
  scope.querySelectorAll("[data-tab-panel]").forEach((p) => (p.hidden = p.dataset.tabPanel !== id));
});

/* Под-вкладки: Список / Канбан */
document.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-subtab]");
  if (!btn) return;
  const scope = btn.closest("[data-subtabs]");
  scope.querySelectorAll("[data-subtab]").forEach((b) => b.classList.toggle("active", b === btn));
  const id = btn.dataset.subtab;
  scope.querySelectorAll("[data-subpanel]").forEach((p) => (p.hidden = p.dataset.subpanel !== id));
});

/* Esc закрывает открытые inline-формы */
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    document.querySelectorAll("details[open]").forEach((d) => d.removeAttribute("open"));
  }
});

/* Автовысота textarea заметок */
function autogrow(ta) {
  ta.style.height = "auto";
  ta.style.height = ta.scrollHeight + 2 + "px";
}
document.addEventListener("input", (e) => {
  const ta = e.target.closest("textarea[data-autogrow]");
  if (ta) autogrow(ta);
});
document.addEventListener("htmx:afterSwap", (e) => {
  e.target.querySelectorAll?.("textarea[data-autogrow]").forEach(autogrow);
});
window.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("textarea[data-autogrow]").forEach(autogrow);
});

/* Глобальный htmx-индикатор загрузки */
document.body.addEventListener("htmx:beforeRequest", () => document.body.classList.add("is-loading"));
document.body.addEventListener("htmx:afterRequest", () => document.body.classList.remove("is-loading"));
document.body.addEventListener("htmx:sendError", () => document.body.classList.remove("is-loading"));

/* Фильтры живут в URL: после любого действия/фильтрации синхронизируем адресную строку,
   чтобы «Поделиться ссылкой» и «Назад» возвращали тот же вид. */
function syncUrlFromFilters() {
  const form = document.getElementById("board-filters") || document.getElementById("filters");
  if (!form || !history.replaceState) return;
  const params = new URLSearchParams();
  new FormData(form).forEach((value, key) => {
    if (String(value).trim()) params.set(key, String(value));
  });
  const qs = params.toString();
  history.replaceState(null, "", location.pathname + (qs ? "?" + qs : ""));
}
document.addEventListener("change", (e) => {
  if (e.target.closest && e.target.closest("#board-filters, #filters")) syncUrlFromFilters();
});
document.addEventListener("input", (e) => {
  if (e.target.closest && e.target.closest("#board-filters, #filters") && e.target.type === "search") {
    clearTimeout(window.__urlSyncTimer);
    window.__urlSyncTimer = setTimeout(syncUrlFromFilters, 400);
  }
});
document.addEventListener("htmx:afterSwap", syncUrlFromFilters);

/* Фокус: после подмены фрагмента kanban возвращаем фокус на ту же карточку/кнопку,
   чтобы смена статуса с клавиатуры не сбрасывала пользователя в начало страницы.
   Ответ может содержать OOB-элементы (chrome), чей afterSwap приходит раньше
   основной цели и не должен «съедать» запомненный фокус — поэтому восстанавливаем
   только там, где реально есть карточка, и подстраховываемся отложенно после свопов. */
let focusMemo = null;
document.addEventListener("htmx:beforeRequest", () => {
  const ae = document.activeElement;
  const card = ae && ae.closest ? ae.closest("[data-task-id]") : null;
  focusMemo = card ? { taskId: card.dataset.taskId, key: ae.dataset.focusKey || null } : null;
});
function restoreFocus(scope) {
  if (!focusMemo) return;
  const memo = focusMemo;
  const root = scope && scope.querySelector ? scope : document;
  const card = root.querySelector(`[data-task-id="${CSS.escape(memo.taskId)}"]`);
  if (!card) return; // это OOB-своп или цель без карточки — ждём основную подмену
  focusMemo = null;
  const btn = memo.key ? card.querySelector(`[data-focus-key="${memo.key}"]`) : null;
  (btn || card).focus();
}
document.addEventListener("htmx:afterSwap", (e) => {
  if (!focusMemo) return;
  restoreFocus(e.target); // OOB-цель карточки не содержит — memo не трогаем
  // Страховка: если main-цель не отдала карточку (или это был только OOB-своп),
  // восстанавливаем после завершения синхронной фазы свопов htmx.
  clearTimeout(window.__focusTimer);
  window.__focusTimer = setTimeout(() => {
    restoreFocus(document);
    focusMemo = null; // не оставляем протухший фокус, если карточка исчезла
  }, 0);
});

/* Перетаскивание карточек задач между колонками канбана.
   Бросок в колонку = POST /ui/tasks/<id>/status с view=kanban|global —
   сервер вернёт обновлённую доску, мы подменяем фрагмент и запускаем
   htmx.process (ручная подмена DOM сама по себе htmx не обрабатывает). */
document.addEventListener("dragstart", (e) => {
  const card = e.target.closest?.(".kanban-card[draggable='true']");
  if (!card) return;
  e.dataTransfer.setData("text/plain", card.dataset.taskId);
  e.dataTransfer.effectAllowed = "move";
  card.classList.add("dragging");
});
document.addEventListener("dragend", (e) => {
  e.target.classList?.remove("dragging");
});
document.addEventListener("dragover", (e) => {
  const col = e.target.closest?.(".kanban-col[data-col]");
  if (!col) return;
  e.preventDefault(); // разрешаем drop
  e.dataTransfer.dropEffect = "move";
  col.classList.add("drop-target");
});
document.addEventListener("dragleave", (e) => {
  const col = e.target.closest?.(".kanban-col[data-col]");
  if (col && !col.contains(e.relatedTarget)) col.classList.remove("drop-target");
});
document.addEventListener("drop", (e) => {
  const col = e.target.closest?.(".kanban-col[data-col]");
  if (!col) return;
  e.preventDefault();
  col.classList.remove("drop-target");
  const taskId = e.dataTransfer.getData("text/plain");
  if (!taskId) return;
  const isGlobal = Boolean(col.closest("#global-kanban"));
  const view = isGlobal ? "global" : "kanban";
  // Фильтры берём с той доски, на которой произошёл бросок (раньше глобальная
  // доска читала поиск карточки проекта — фильтры после drag-and-drop терялись).
  const q = isGlobal
    ? document.querySelector('#board-filters [name="q"]')?.value || ""
    : document.querySelector('.task-search[name="q"]')?.value || "";
  const projectFilter = document.querySelector('#board-filters [name="project_id"]');
  const params = new URLSearchParams({
    status: col.dataset.col,
    view: view,
    q: q,
    project_id: isGlobal ? (projectFilter?.value || "") : "",
  });
  fetch(`/ui/tasks/${taskId}/status`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: params.toString(),
  })
    .then((r) => r.text())
    .then((html) => {
      const targetId = isGlobal ? "tasks-board" : "kanban-block";
      const target = document.getElementById(targetId);
      const wrap = document.createElement("div");
      wrap.innerHTML = html;
      const fresh = wrap.firstElementChild;
      if (target && fresh && fresh.id === targetId) {
        target.replaceWith(fresh);
        window.htmx?.process(fresh); // свежая разметка содержит hx-атрибуты
        syncUrlFromFilters();
      } else {
        window.location.reload(); // структура не совпала — надёжнее перерисовать
      }
    })
    .catch(() => window.location.reload());
});
