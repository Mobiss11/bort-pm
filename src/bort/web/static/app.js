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

/* Перетаскивание карточек задач между колонками канбана.
   Бросок в колонку = POST /ui/tasks/<id>/status с view=kanban|global —
   сервер вернёт обновлённую доску, htmx сам подменит фрагмент. */
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
  // Сохраняем фильтры доски (проект/поиск), чтобы доска после броска не сбрасывалась
  const q = document.querySelector('.task-search[name="task-q"]')?.value || "";
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
      const targetId = isGlobal ? "global-kanban" : "kanban-block";
      const target = document.getElementById(targetId);
      const wrap = document.createElement("div");
      wrap.innerHTML = html;
      const fresh = wrap.firstElementChild;
      if (target && fresh && fresh.id === targetId) {
        target.replaceWith(fresh);
      } else {
        window.location.reload(); // структура не совпала — надёжнее перерисовать
      }
    })
    .catch(() => window.location.reload());
});
