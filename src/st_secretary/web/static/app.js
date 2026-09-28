/* СТ-Секретарь: немного поведения на страницах. Без библиотек — всё работает без интернета. */
(function () {
  "use strict";

  // --- Несохранённые изменения: предупредить, если человек уходит со страницы, не нажав «Сохранить».
  var dirty = false;
  function markDirty() { dirty = true; }
  document.querySelectorAll("form[data-guard]").forEach(function (form) {
    form.addEventListener("input", markDirty);
    form.addEventListener("change", markDirty);
    form.addEventListener("submit", function () { dirty = false; });
  });
  window.addEventListener("beforeunload", function (e) {
    if (dirty) { e.preventDefault(); e.returnValue = ""; }
  });

  // --- Файлы заявок: перетаскивание или выбор — и сразу отправка.
  document.querySelectorAll("[data-dropzone]").forEach(function (zone) {
    var input = zone.querySelector("input[type=file]");
    function send() { zone.classList.add("is-busy"); zone.submit(); }
    input.addEventListener("change", function () { if (input.files.length) send(); });
    ["dragenter", "dragover"].forEach(function (ev) {
      zone.addEventListener(ev, function (e) { e.preventDefault(); zone.classList.add("is-over"); });
    });
    ["dragleave", "drop"].forEach(function (ev) {
      zone.addEventListener(ev, function (e) { e.preventDefault(); zone.classList.remove("is-over"); });
    });
    zone.addEventListener("drop", function (e) {
      if (e.dataTransfer && e.dataTransfer.files.length) { input.files = e.dataTransfer.files; send(); }
    });
  });
  // Файл, брошенный мимо зоны, браузер не должен открывать вместо страницы.
  window.addEventListener("dragover", function (e) { e.preventDefault(); });
  window.addEventListener("drop", function (e) { e.preventDefault(); });

  // --- Строки ГСК и блоки зачётов: добавить и удалить.
  document.querySelectorAll("[data-add-row]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var box = document.getElementById(btn.dataset.addRow);
      var tpl = document.getElementById(btn.dataset.template);
      var i = Number(box.dataset.next || 0);
      box.dataset.next = i + 1;
      box.insertAdjacentHTML("beforeend", tpl.innerHTML.replace(/__i__/g, String(i)));
      var first = box.lastElementChild && box.lastElementChild.querySelector("input:not([type=hidden]), select");
      if (first) first.focus();
      markDirty();
    });
  });
  document.addEventListener("click", function (e) {
    var rm = e.target.closest("[data-remove-row]");
    if (!rm) return;
    var row = rm.closest("[data-row]");
    if (row && window.confirm(rm.dataset.confirm || "Удалить?")) { row.remove(); markDirty(); }
  });

  // --- Подтверждение перед действием (например, «Убрать заявку»).
  document.querySelectorAll("form[data-confirm]").forEach(function (form) {
    form.addEventListener("submit", function (e) {
      if (!window.confirm(form.dataset.confirm)) e.preventDefault();
    });
  });

  // --- Долгие действия: показать, что идёт работа.
  document.querySelectorAll("form[data-busy]").forEach(function (form) {
    form.addEventListener("submit", function () {
      var b = form.querySelector("button[type=submit]");
      if (!b) return;
      setTimeout(function () { b.classList.add("is-busy"); b.lastChild.textContent = form.dataset.busy; }, 0);
    });
  });
  window.addEventListener("pageshow", function () {  // возврат кнопкой «Назад» — кнопки снова активны
    document.querySelectorAll(".is-busy").forEach(function (el) { el.classList.remove("is-busy"); });
  });

  // --- Вкладки.
  document.querySelectorAll("[role=tablist]").forEach(function (list) {
    var tabs = Array.prototype.slice.call(list.querySelectorAll("[role=tab]"));
    var key = "tab:" + location.pathname;
    function select(tab) {
      tabs.forEach(function (t) {
        var on = t === tab;
        t.setAttribute("aria-selected", on ? "true" : "false");
        document.getElementById(t.getAttribute("aria-controls")).hidden = !on;
      });
      try { sessionStorage.setItem(key, tab.id); } catch (e) { /* без хранилища — просто не запоминаем */ }
    }
    tabs.forEach(function (t) { t.addEventListener("click", function () { select(t); }); });
    try {
      var saved = document.getElementById(sessionStorage.getItem(key));
      if (saved && tabs.indexOf(saved) >= 0) select(saved);
    } catch (e) { /* нет — остаётся первая вкладка */ }
  });

  // --- Фильтр команд по статусу заявки: все / ошибки / проверить / проверено (запоминается до закрытия вкладки).
  var teamFilters = Array.prototype.slice.call(document.querySelectorAll("[data-team-filter]"));
  function filterTeams(value) {
    var shown = 0;
    teamFilters.forEach(function (b) { b.setAttribute("aria-pressed", b.dataset.teamFilter === value ? "true" : "false"); });
    document.querySelectorAll("article.team[data-status]").forEach(function (t) {
      t.hidden = value !== "all" && t.dataset.status !== value;
      if (!t.hidden) shown++;
    });
    var empty = document.querySelector(".filter-empty");
    if (empty) empty.hidden = shown > 0;
    try { sessionStorage.setItem("teams:" + location.pathname, value); } catch (e) { /* не запоминаем */ }
  }
  if (teamFilters.length) {
    teamFilters.forEach(function (b) { b.addEventListener("click", function () { filterTeams(b.dataset.teamFilter); }); });
    var savedFilter = null;
    try { savedFilter = sessionStorage.getItem("teams:" + location.pathname); } catch (e) { /* нет */ }
    if (savedFilter && teamFilters.some(function (b) { return b.dataset.teamFilter === savedFilter; })) filterTeams(savedFilter);
  }
  document.querySelectorAll("[data-show-fixed]").forEach(function (box) {
    box.addEventListener("change", function () {
      document.querySelectorAll('[data-sev="fixed"]').forEach(function (el) { el.hidden = !box.checked; });
    });
  });

  // --- Комиссия по допуску: каждое изменение сохраняется сразу; сервер присылает обновлённый блок команды
  //     и итоги. Без скриптов те же формы отправляются кнопкой «Сохранить».
  document.documentElement.classList.add("js");
  function autosave(form, extra) {
    var fd = new FormData(form);
    if (extra && extra.name) fd.append(extra.name, extra.value);
    var note = form.querySelector(".adm-saved");
    if (note) note.textContent = "Сохраняю…";
    // адрес — из атрибута: поле формы с именем «action» подменило бы свойство form.action
    fetch(form.getAttribute("action"), { method: "POST", body: fd, headers: { "X-Autosave": "1" } })
      .then(function (r) { if (!r.ok) throw new Error(String(r.status)); return r.json(); })
      .then(function (j) {
        var article = form.closest("article");
        var active = document.activeElement;
        var focusName = active && article.contains(active) ? active.name : null;
        var box = document.createElement("div");
        box.innerHTML = j.team;
        var fresh = box.querySelector("article");
        article.replaceWith(fresh);
        var tiles = document.getElementById("adm-tiles");
        if (tiles && j.tiles) {
          var tb = document.createElement("div");
          tb.innerHTML = j.tiles;
          tiles.replaceWith(tb.querySelector("#adm-tiles"));
        }
        var saved = fresh.querySelector(".adm-saved");
        if (saved) saved.textContent = "Сохранено в " + j.saved;
        Object.keys(j.by || {}).forEach(function (k) {  // числа на кнопках фильтра
          var b = document.querySelector('[data-team-filter="' + k + '"] b');
          if (b) b.textContent = j.by[k];
        });
        if (focusName) {
          var el = fresh.querySelector('[name="' + focusName + '"]');
          if (el) el.focus();
        }
      })
      .catch(function () {
        if (note) note.textContent = "Не сохранилось — нажмите «Сохранить»";
        form.classList.add("adm-failed");
      });
  }
  document.addEventListener("change", function (e) {
    var form = e.target.closest ? e.target.closest("form[data-autosave]") : null;
    if (form) autosave(form);
  });
  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (!form.matches || !form.matches("form[data-autosave]") || form.classList.contains("adm-failed")) return;
    e.preventDefault();  // Enter в поле или кнопка «Отметить все документы» — тоже без перезагрузки
    autosave(form, e.submitter ? { name: e.submitter.name, value: e.submitter.value } : null);
  });

  // --- Карточка и форма заявки: запомнить команду — список заявок потом откроется сразу на ней.
  var mark = document.querySelector("[data-team-anchor]");
  if (mark) {
    try { sessionStorage.setItem("lastTeam:" + mark.dataset.listPath, mark.dataset.teamAnchor); } catch (e) { /* нет */ }
  }

  // --- Список заявок: открыть на команде, с которой вернулись (метка #t-… в адресе или память вкладки),
  //     чтобы не крутить страницу; если команду скрывает фильтр — на следующей видимой.
  var teamList = document.querySelector("[data-team-list]");
  if (teamList) {
    var id = location.hash.slice(1);
    if (!id) {
      var from = "";
      try { from = new URL(document.referrer).pathname; } catch (e) { /* пришли не со страницы программы */ }
      if (from.indexOf(location.pathname + "/") === 0) {  // из карточки или формы заявки этого соревнования
        try { id = sessionStorage.getItem("lastTeam:" + location.pathname) || ""; } catch (e) { /* нет */ }
      }
    }
    var target = id ? document.getElementById(id) : null;
    if (target && target.matches("article.team")) {
      var tab = document.getElementById(teamList.getAttribute("aria-labelledby"));
      if (teamList.hidden && tab) tab.click();  // была открыта другая вкладка — вернуть «Команды»
      var t = target;
      while (t && (t.hidden || !t.matches("article.team"))) t = t.nextElementSibling;
      if (!t) {  // ниже видимых нет — ближайшая выше
        t = target;
        while (t && (t.hidden || !t.matches("article.team"))) t = t.previousElementSibling;
      }
      if (t) {
        var go = function () { t.scrollIntoView({ block: "start" }); };
        go();
        window.addEventListener("load", go);  // браузер может сам прокрутить к метке позже — поправить
        t.classList.add("is-recent");
        setTimeout(function () { t.classList.remove("is-recent"); }, 2500);
      }
    }
  }

  // --- Фильтр замечаний: ошибки / проверить / проверено / исправлено.
  document.querySelectorAll("[data-filter]").forEach(function (chip) {
    chip.addEventListener("click", function () {
      var on = chip.getAttribute("aria-pressed") !== "true";
      chip.setAttribute("aria-pressed", on ? "true" : "false");
      document.querySelectorAll('[data-sev="' + chip.dataset.filter + '"]').forEach(function (el) { el.hidden = !on; });
    });
  });
})();
