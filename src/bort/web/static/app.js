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

/* Вкладка задач загружается отдельно: при недоступности bd/сети не оставляем вечную загрузку. */
document.addEventListener("htmx:afterSwap", (e) => {
  if (e.target?.id === "beads-content") e.target.removeAttribute("aria-busy");
});
function beadsLoadError(e) {
  const target = e.detail?.target;
  if (target?.id !== "beads-content") return;
  target.removeAttribute("aria-busy");
  const notice = target.querySelector(".beads-loading");
  if (notice) notice.textContent = "Не удалось загрузить задачи. Обновите страницу для повторной попытки.";
}
document.addEventListener("htmx:responseError", beadsLoadError);
document.addEventListener("htmx:sendError", beadsLoadError);

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

/* Перетаскивание карточек задач между колонками канбана проекта.
   Бросок в колонку = POST /ui/tasks/<id>/status с view=kanban —
   сервер вернёт обновлённую доску, мы подменяем фрагмент и запускаем
   htmx.process (ручная подмена DOM сама по себе htmx не обрабатывает). */
document.addEventListener("dragstart", (e) => {
  const card = e.target.closest?.(".kanban-card[draggable='true']");
  if (!card) return;
  e.dataTransfer.setData("text/plain", card.dataset.taskId);
  e.dataTransfer.effectAllowed = "move";
  card.classList.add("dragging");
});document.addEventListener("dragend", (e) => {
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
  const q = document.querySelector('.task-search[name="q"]')?.value || "";
  const params = new URLSearchParams({
    status: col.dataset.col,
    view: "kanban",
    q: q,
  });
  fetch(`/ui/tasks/${taskId}/status`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: params.toString(),
  })
    .then((r) => r.text())
    .then((html) => {
      const target = document.getElementById("kanban-block");
      const wrap = document.createElement("div");
      wrap.innerHTML = html;
      const fresh = wrap.firstElementChild;
      if (target && fresh && fresh.id === "kanban-block") {
        target.replaceWith(fresh);
        window.htmx?.process(fresh); // свежая разметка содержит hx-атрибуты
      } else {
        window.location.reload(); // структура не совпала — надёжнее перерисовать
      }
    })
    .catch(() => window.location.reload());
});

/* Ручной порядок проектов в сводке: перетаскивание за ручку, стрелки ↑/↓ на ней
   и кнопки ↑/↓ для тач-экранов. Все три способа отправляют один и тот же POST со
   списком id; сервер возвращает готовую доску, куда возвращаем фокус на ручку. */
(() => {
  const box = () => document.getElementById("projects-table-box");
  const rows = () => Array.from(document.querySelectorAll("#projects-tbody tr.project-row[data-project-id]"));
  const ids = () => rows().map((r) => r.dataset.projectId);

  function announce(text) {
    const live = document.getElementById("summary-live");
    if (live) live.textContent = text;
  }

  function boardParams() {
    const url = new URLSearchParams(window.location.search);
    return {
      view: url.get("view") === "kanban" ? "kanban" : "list",
      q: url.get("q") || "",
      tasks: url.get("tasks") || "",
    };
  }

  function focusHandle(projectId) {
    const handle = box()?.querySelector(`.drag-handle[data-project-id="${CSS.escape(projectId)}"]`);
    if (handle) handle.focus();
  }

  function sendOrder(list, projectId) {
    const target = box();
    if (!target || !list.length) return;
    const params = new URLSearchParams(boardParams());
    list.forEach((id) => params.append("ids", id));
    target.setAttribute("aria-busy", "true");
    fetch("/ui/projects/reorder", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: params.toString(),
    })
      .then((r) => {
        if (!r.ok) {
          window.location.reload(); // несовпадение порядка: надёжнее перерисовать
          return null;
        }
        return r.text();
      })
      .then((html) => {
        if (html === null) return;
        target.innerHTML = html;
        window.htmx?.process(target); // свежая разметка содержит hx-атрибуты
        focusHandle(projectId);
        announce("Порядок проектов обновлён");
      })
      .catch(() => window.location.reload())
      .finally(() => target.removeAttribute("aria-busy"));
  }

  function moveRow(handle, delta) {
    const list = rows();
    const row = handle.closest("tr.project-row");
    const from = list.indexOf(row);
    const to = from + delta;
    if (from < 0 || to < 0 || to >= list.length) return;
    const order = ids();
    [order[from], order[to]] = [order[to], order[from]];
    sendOrder(order, handle.dataset.projectId);
  }

  document.addEventListener("keydown", (e) => {
    const handle = e.target.closest?.(".drag-handle");
    if (!handle || (e.key !== "ArrowUp" && e.key !== "ArrowDown")) return;
    e.preventDefault();
    moveRow(handle, e.key === "ArrowUp" ? -1 : 1);
  });

  document.addEventListener("click", (e) => {
    const btn = e.target.closest?.(".row-move .btn");
    if (!btn) return;
    const handle = btn.closest("tr.project-row")?.querySelector(".drag-handle");
    if (handle) moveRow(handle, btn.dataset.move === "up" ? -1 : 1);
  });

  document.addEventListener("dragstart", (e) => {
    const handle = e.target.closest?.(".drag-handle");
    const row = handle?.closest("tr.project-row");
    if (!handle || !row) return;
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", row.dataset.projectId);
    row.classList.add("row-dragging");
  });

  function clearDropMarks() {
    document.querySelectorAll(".project-row.drop-before, .project-row.drop-after")
      .forEach((el) => el.classList.remove("drop-before", "drop-after"));
  }

  document.addEventListener("dragover", (e) => {
    if (!e.target.closest?.("#projects-tbody")) return;
    e.preventDefault(); // разрешаем drop
    e.dataTransfer.dropEffect = "move";
    clearDropMarks();
    const row = e.target.closest("tr.project-row");
    if (!row || row.classList.contains("row-dragging")) return;
    const rect = row.getBoundingClientRect();
    row.classList.add(e.clientY > rect.top + rect.height / 2 ? "drop-after" : "drop-before");
  });

  document.addEventListener("dragend", (e) => {
    e.target.closest?.("tr.project-row")?.classList.remove("row-dragging");
    clearDropMarks();
  });

  document.addEventListener("drop", (e) => {
    if (!e.target.closest?.("#projects-tbody")) return;
    e.preventDefault();
    clearDropMarks();
    const draggedId = e.dataTransfer.getData("text/plain");
    const list = rows();
    const from = list.findIndex((r) => r.dataset.projectId === draggedId);
    if (from < 0) return;
    const row = e.target.closest("tr.project-row");
    let to = list.length;
    if (row && !row.classList.contains("row-dragging")) {
      const rect = row.getBoundingClientRect();
      to = list.indexOf(row) + (e.clientY > rect.top + rect.height / 2 ? 1 : 0);
    }
    if (to > from) to -= 1; // сначала убрать, потом вставить
    if (to === from) return;
    const order = ids();
    order.splice(from, 1);
    order.splice(to, 0, draggedId);
    sendOrder(order, draggedId);
  });
})();

/* Раздел задач: черновики и раскрывашки переживают htmx-подмену #beads-content
   (автообновление при ожидающих командах). После успешного создания задачи
   сервер помечает форму data-draft-reset — её черновик очищается. */
(() => {
  let drafts = null;

  function keyFor(form, field) {
    const formName = form.dataset.draft || form.className.split(" ")[0] || "form";
    const ident =
      form.querySelector('input[name="issue"]')?.value ||
      form.querySelector('input[name="name"]')?.value || "";
    const same = Array.from(form.querySelectorAll(field.tagName.toLowerCase() + '[name="' + CSS.escape(field.name) + '"]'));
    return formName + "|" + ident + "|" + field.name + "|" + same.indexOf(field);
  }

  function capture(root) {
    const values = {};
    root.querySelectorAll("input, select, textarea").forEach((el) => {
      const form = el.closest("form");
      if (form) values[keyFor(form, el)] = el.value;
    });
    const details = {};
    root.querySelectorAll("details[data-draft]").forEach((el) => {
      details[el.dataset.draft] = el.open;
    });
    return { values, details };
  }

  function restore(root, data) {
    if (!data) return;
    const marker = root.querySelector("[data-draft-reset]");
    const reset = new Set((marker?.dataset.draftReset || "").split(",").filter(Boolean));
    root.querySelectorAll("input, select, textarea").forEach((el) => {
      const form = el.closest("form");
      if (!form) return;
      const draftName = el.closest("details[data-draft]")?.dataset.draft;
      if (draftName && reset.has(draftName)) return; // свежее состояние важнее черновика
      const key = keyFor(form, el);
      if (key in data.values) el.value = data.values[key];
    });
    root.querySelectorAll("details[data-draft]").forEach((el) => {
      if (reset.has(el.dataset.draft)) { el.open = false; return; }
      if (el.dataset.draft in data.details) el.open = data.details[el.dataset.draft];
    });
  }

  document.addEventListener("htmx:beforeSwap", (e) => {
    if (e.target?.id === "beads-content") drafts = capture(e.target);
  });
  document.addEventListener("htmx:afterSwap", (e) => {
    if (e.target?.id !== "beads-content") return;
    restore(e.target, drafts);
    drafts = null;
  });
})();
