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

  // --- Выбрал файл — сразу отправить (загрузка протокола).
  document.querySelectorAll("input[data-autosubmit]").forEach(function (input) {
    input.addEventListener("change", function () { if (input.files.length) input.form.submit(); });
  });

  // --- Подтверждение перед действием (например, «Убрать заявку»).
  document.querySelectorAll("form[data-confirm]").forEach(function (form) {
    form.addEventListener("submit", function (e) {
      if (!window.confirm(form.dataset.confirm)) e.preventDefault();
    });
  });

  // --- Подтверждение у отдельной кнопки (например, «Провести жеребьёвку» заново).
  document.querySelectorAll("button[data-confirm]").forEach(function (b) {
    b.addEventListener("click", function (e) {
      if (!window.confirm(b.dataset.confirm)) e.preventDefault();
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
        var tiles = document.querySelector("[data-live-tiles]");
        if (tiles && j.tiles) {
          var tb = document.createElement("div");
          tb.innerHTML = j.tiles;
          tiles.replaceWith(tb.querySelector("[data-live-tiles]"));
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

  // --- Баллы по этапам: сохраняются сразу; поля ввода не перерисовываются (набор не теряется) — обновляются
  //     только суммы, места и таблица результатов. Enter — на команду ниже, как в Excel.
  document.querySelectorAll("form[data-points]").forEach(function (form) {
    var note = form.querySelector(".adm-saved");
    function save() {
      if (note) note.textContent = "Сохраняю…";
      fetch(form.getAttribute("action"), { method: "POST", body: new FormData(form), headers: { "X-Autosave": "1" } })
        .then(function (r) { if (!r.ok) throw new Error(String(r.status)); return r.json(); })
        .then(function (j) {
          form.querySelectorAll("tr[data-team]").forEach(function (row) {
            var c = j.cells[row.dataset.team];
            if (!c) return;
            row.querySelector("[data-total]").textContent = c.total;
            row.querySelector("[data-place]").textContent = c.place;
            row.querySelectorAll("input[data-stage]").forEach(function (inp) {
              inp.classList.toggle("is-invalid", c.bad.indexOf(inp.dataset.stage) >= 0);
            });
          });
          var live = document.querySelector("[data-live-results]");
          if (live && j.results) {
            var box = document.createElement("div");
            box.innerHTML = j.results;
            live.replaceWith(box.querySelector("[data-live-results]"));
          }
          if (note) note.textContent = "Сохранено в " + j.saved;
        })
        .catch(function () { if (note) note.textContent = "Не сохранилось — нажмите «Сохранить»"; });
    }
    form.addEventListener("change", save);
    form.addEventListener("submit", function (e) { e.preventDefault(); save(); });
    form.addEventListener("keydown", function (e) {
      if (e.key !== "Enter" || !e.target.matches("input[data-stage]")) return;
      e.preventDefault();
      var cell = e.target.closest("td"), row = cell.parentElement;
      var idx = Array.prototype.indexOf.call(row.children, cell);
      var next = e.shiftKey ? row.previousElementSibling : row.nextElementSibling;
      var inp = next && next.children[idx] && next.children[idx].querySelector("input");
      if (inp) { inp.focus(); inp.select(); } else { e.target.blur(); }
    });
  });

  // --- Документы команды: переключение, поворот фото (с телефона они часто лёжа), увеличение по щелчку.
  document.querySelectorAll("[data-doc-viewer]").forEach(function (pane) {
    var view = pane.querySelector("[data-doc-view]");
    var rot = 0;
    function show(btn) {
      pane.querySelectorAll(".doc-tab").forEach(function (b) { b.setAttribute("aria-selected", b === btn ? "true" : "false"); });
      rot = 0;
      view.innerHTML = "";
      var url = btn.dataset.docUrl, kind = btn.dataset.docKind;
      if (kind === "image") {
        var img = new Image();
        img.src = url;
        img.alt = btn.dataset.docName;
        img.className = "doc-img";
        img.title = "Щёлкните, чтобы увеличить";
        img.addEventListener("click", function () { img.classList.toggle("is-zoom"); });
        view.appendChild(img);
      } else if (kind === "pdf") {
        var fr = document.createElement("iframe");
        fr.src = url;
        fr.title = btn.dataset.docName;
        fr.className = "doc-pdf";
        view.appendChild(fr);
      } else {
        view.innerHTML = '<p class="muted">Этот файл браузер не показывает — нажмите «Открыть на компьютере».</p>';
      }
      pane.querySelectorAll("[data-doc-current]").forEach(function (i) { i.value = btn.dataset.docName; });
    }
    var tabs = Array.prototype.slice.call(pane.querySelectorAll(".doc-tab"));
    tabs.forEach(function (b) { b.addEventListener("click", function () { show(b); }); });
    pane.querySelectorAll("[data-doc-step]").forEach(function (btn) {  // листать документы подряд
      btn.addEventListener("click", function () {
        var cur = tabs.findIndex(function (b) { return b.getAttribute("aria-selected") === "true"; });
        var next = tabs[(cur + Number(btn.dataset.docStep) + tabs.length) % tabs.length];
        if (next) show(next);
      });
    });
    var r = pane.querySelector("[data-doc-rotate]");
    if (r) r.addEventListener("click", function () {
      var img = view.querySelector("img");
      if (!img) return;
      rot = (rot + 90) % 360;
      img.style.transform = "rotate(" + rot + "deg)";
    });
    var first = pane.querySelector(".doc-tab");
    if (first) show(first);
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

  // --- Новая версия: GitHub спрашивают в фоне при запуске — главная узнаёт ответ у программы, когда он придёт.
  var updSlot = document.querySelector("[data-update-banner]");
  if (updSlot) {
    var updTries = 0;
    (function ask() {
      fetch(updSlot.dataset.updateBanner, { cache: "no-store" }).then(function (r) {
        if (r.status === 202 && ++updTries < 10) { setTimeout(ask, 2000); return null; }
        return r.status === 200 ? r.text() : null;
      }).then(function (html) { if (html) updSlot.innerHTML = html; }).catch(function () { /* без ответа — молчим */ });
    })();
  }

  // --- Страница обновления: ход скачивания, потом перезапуск и переход на новую версию.
  var upd = document.getElementById("upd");
  if (upd && upd.querySelector("[data-upd-bar]")) {
    var bar = upd.querySelector("[data-upd-bar]");
    var title = upd.querySelector("[data-upd-title]");
    var text = upd.querySelector("[data-upd-text]");
    var want = upd.dataset.version;
    var titles = { backup: "Делаю резервную копию…", download: "Скачиваю новую версию…",
                   unpack: "Распаковываю…", ready: "Перезапускаю программу…", restart: "Перезапускаю программу…" };
    function show(state) {
      upd.dataset.state = state;
      upd.querySelectorAll("[data-upd-show]").forEach(function (el) {
        el.hidden = el.dataset.updShow.split(" ").indexOf(state) < 0;
      });
      if (titles[state]) title.textContent = titles[state];
    }
    function waitNew(started) {  // программа перезапускается: ждать, пока ответит новая версия
      fetch("/health", { cache: "no-store" }).then(function (r) { return r.json(); }).then(function (h) {
        if (h.version === want) { location.href = "/?done=updated"; return; }
        setTimeout(function () { waitNew(started); }, 1000);
      }).catch(function () {
        if (Date.now() - started > 90000) {
          text.textContent = "Программа долго не отвечает. Посмотрите " + upd.dataset.console + ": если оно " +
                             "закрылось — запустите «" + upd.dataset.launcher + "» снова.";
        }
        setTimeout(function () { waitNew(started); }, 1000);
      });
    }
    function poll() {
      fetch("/update/status", { cache: "no-store" }).then(function (r) { return r.json(); }).then(function (s) {
        show(s.state);
        if (s.state === "download" && s.total) {
          bar.style.width = Math.min(100, Math.round(100 * s.done / s.total)) + "%";
          text.textContent = "Скачано " + (s.done / 1048576).toFixed(1) + " из " + (s.total / 1048576).toFixed(1) +
                             " МБ. Не закрывайте " + upd.dataset.console + " программы.";
        } else if (s.state === "unpack") {
          bar.style.width = "100%";
        } else if (s.state === "ready") {
          bar.style.width = "100%";
          text.textContent = "Новая версия готова. Программа перезапускается — страница откроется сама через полминуты.";
          fetch("/update/restart", { method: "POST" }).finally(function () { waitNew(Date.now()); });
          return;
        } else if (s.state === "error") {
          var err = upd.querySelector("[data-upd-error]");
          if (err) err.textContent = "Не получилось: " + s.error + ".";
          return;
        } else if (s.state === "idle") {
          return;
        }
        setTimeout(poll, 700);
      }).catch(function () { setTimeout(poll, 2000); });
    }
    if (upd.dataset.state !== "idle" && upd.dataset.state !== "error") poll();
  }
})();
