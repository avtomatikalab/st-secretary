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
      box.lastElementChild.querySelectorAll("textarea[data-autosize]").forEach(function (t) { t.style.height = "auto"; });
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
          // выбрали «допустить решением комиссии» — сразу в поле основания
          var need = /decision$/.test(focusName) ? fresh.querySelector("[data-need-reason]") : null;
          if (need) need.focus(); else if (el) el.focus();
        }
      })
      .catch(function () {
        if (note) note.textContent = "Не сохранилось — нажмите «Сохранить»";
        form.classList.add("adm-failed");
      });
  }
  // «Допустить решением комиссии», когда заявка или документы не проверены, — только с подтверждением
  document.addEventListener("change", function (e) {
    var sel = e.target;
    if (!sel.matches || !sel.matches("select[data-confirm-admit]") || sel.value !== "admitted") return;
    if (window.confirm(sel.dataset.confirmAdmit)) return;
    var was = Array.prototype.filter.call(sel.options, function (o) { return o.defaultSelected; })[0];
    sel.value = was ? was.value : "";
    e.stopPropagation();  // не сохранять
  }, true);
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
    form.addEventListener("submit", function (e) {
      if (e.submitter && e.submitter.hasAttribute("formaction")) return;  // «снята ✕» — своя отправка, со страницей
      e.preventDefault(); save();
    });
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

  // --- Карточка: галочка «Неофициальные соревнования» показывает поля своих зачётов и дисциплин
  document.querySelectorAll("[data-unofficial]").forEach(function (cb) {
    cb.addEventListener("change", function () {
      cb.form.classList.toggle("is-unofficial", cb.checked);
      var own = document.getElementById("own-values"); if (own) own.classList.toggle("own-hidden", !cb.checked);
    });
  });
  // соревнование фестиваля: поправили человека в строке ГСК — это «своя» замена в этом соревновании
  document.addEventListener("input", function (e) {
    var row = e.target.closest && e.target.closest("tr[data-own-row]");
    if (!row || e.target.matches('input[name$="-own"]')) return;
    var own = row.querySelector('input[name$="-own"]');
    if (own && !own.checked) { own.checked = true; row.className = "gsk-own"; }
  });
  document.addEventListener("change", function (e) {
    if (!e.target.matches || !e.target.matches('tr[data-own-row] input[name$="-own"]')) return;
    e.target.closest("tr").className = e.target.checked ? "gsk-own" : "gsk-common";
  });
  // своя дисциплина из запомненных — подставить её вид результата и состав
  document.addEventListener("change", function (e) {
    if (!e.target.matches || !e.target.matches('input[name$="-discipline_text"]')) return;
    var opt = Array.prototype.filter.call(document.querySelectorAll("#dl-disc option"),
      function (o) { return o.value === e.target.value.trim(); })[0];
    var block = e.target.closest(".zachet");
    if (!opt || !block) return;
    [["result", opt.dataset.result], ["unit", opt.dataset.unit]].forEach(function (p) {
      var sel = block.querySelector('select[name$="-' + p[0] + '"]'); if (sel && !sel.value) sel.value = p[1];
    });
  });

  // --- Значки «Проверить» / «Ошибка» нажимаются: фильтр замечаний (?only=), фильтр заявок (?show=),
  // к первому полю с замечанием (data-jump), к кнопке «Проверено» у замечания (data-to-check).
  function onlyFilter(sev) {
    var chips = document.querySelectorAll("[data-filter]");
    if (!chips.length) return;
    chips.forEach(function (chip) {
      var want = chip.dataset.filter === sev;
      if ((chip.getAttribute("aria-pressed") === "true") !== want) chip.click();
    });
    var box = document.getElementById("issues");
    if (box) box.scrollIntoView({ block: "start" });
  }
  var qs = new URLSearchParams(location.search);
  if (qs.get("only")) onlyFilter(qs.get("only"));
  if (qs.get("show") && typeof filterTeams === "function" && teamFilters.length) filterTeams(qs.get("show"));
  document.addEventListener("click", function (e) {
    var only = e.target.closest("a[data-only]");
    if (only) { e.preventDefault(); onlyFilter(only.dataset.only); return; }
    var show = e.target.closest("a[data-show]");
    if (show && teamFilters.length) {
      e.preventDefault(); filterTeams(show.dataset.show);
      var list = document.getElementById("teams"); if (list) list.scrollIntoView({ block: "start" });
      return;
    }
    var jump = e.target.closest("a[data-jump]");
    if (jump) {
      e.preventDefault();
      var el = document.querySelector(jump.dataset.jump);
      if (el) {
        el.scrollIntoView({ block: "center" });
        var inp = el.matches("input, select, textarea") ? el : el.querySelector("input, select, textarea");
        if (inp) inp.focus({ preventScroll: true });
        el.classList.add("is-target"); setTimeout(function () { el.classList.remove("is-target"); }, 3000);
      }
      return;
    }
    var tc = e.target.closest("[data-to-check]");
    if (tc) {
      var btn = tc.closest("li") && tc.closest("li").querySelector(".btn-check");
      if (btn) {
        btn.scrollIntoView({ block: "center" }); btn.focus();
        btn.classList.add("is-target"); setTimeout(function () { btn.classList.remove("is-target"); }, 3000);
      }
    }
  });

  // --- Поле, которое растёт по тексту (длинное название этапа переносится, а не обрезается).
  function fitHeight(el) {
    if (!el.offsetParent) return;  // в свёрнутом разделе высоту не посчитать — пересчитаем при раскрытии
    el.style.height = "auto";
    el.style.height = el.scrollHeight + 2 + "px";
  }
  document.querySelectorAll("textarea[data-autosize]").forEach(fitHeight);
  document.addEventListener("input", function (e) { if (e.target.matches("textarea[data-autosize]")) fitHeight(e.target); });
  document.addEventListener("toggle", function (e) {
    if (e.target.open) e.target.querySelectorAll("textarea[data-autosize]").forEach(fitHeight);
  }, true);
  window.addEventListener("resize", function () { document.querySelectorAll("textarea[data-autosize]").forEach(fitHeight); });

  // --- «Исправить» у замечания: перейти прямо к полю (?focus=имя или id; «блок/поле» — поле внутри блока с этим id),
  // раскрыть свёрнутые разделы, прокрутить, поставить курсор и подсветить. На той же странице — без перезагрузки.
  function focusTarget(spec) {
    if (!spec) return false;
    var scope = document, name = spec, k = spec.indexOf("/");
    if (k > 0) { scope = document.getElementById(spec.slice(0, k)) || document; name = spec.slice(k + 1); }
    var el = null;
    try {
      el = (scope === document ? document.getElementById(name) : scope.querySelector("#" + CSS.escape(name))) ||
           scope.querySelector('[name="' + name.replace(/["\\]/g, "\\$&") + '"]');
    } catch (e) { el = null; }
    if (!el) return false;
    for (var d = el.closest("details"); d; d = d.parentElement ? d.parentElement.closest("details") : null) d.open = true;
    el.scrollIntoView({ block: "center" });
    if (!el.matches("input, select, textarea, button, a")) el.setAttribute("tabindex", "-1");
    try { el.focus({ preventScroll: true }); } catch (e) { el.focus(); }
    el.classList.add("is-target");
    setTimeout(function () { el.classList.remove("is-target"); }, 3000);
    return true;
  }
  var focusQ = new URLSearchParams(location.search).get("focus");
  if (focusQ) {
    var focusLater = function () { setTimeout(function () { focusTarget(focusQ); }, 50); };
    if (document.readyState === "complete") focusLater(); else window.addEventListener("load", focusLater);
  }
  document.addEventListener("click", function (e) {
    var a = e.target.closest("a.js-fix");
    if (!a || a.origin !== location.origin || a.pathname !== location.pathname) return;
    var here = new URLSearchParams(location.search), there = new URLSearchParams(a.search);
    if ((there.get("z") || "") !== (here.get("z") || "") || (there.get("key") || "") !== (here.get("key") || "")) return;
    if (focusTarget(there.get("focus") || a.hash.slice(1))) e.preventDefault();
  });

  // --- Порядок старта: строки перетаскиваются за «ручку» (мышь и палец — pointer events, клавиатура — стрелки).
  // Номера «Порядок» и время «по расчёту» (первый старт + интервал × место) пересчитываются сразу; сохраняет
  // прежняя кнопка — сервер берёт порядок из полей pos-i. Вписанное вручную время остаётся за командой.
  document.querySelectorAll("table[data-reorder]").forEach(function (table) {
    var tbody = table.tBodies[0];
    function secs(text) {  // «10:00», «9.30», «10:00:30» → секунды; иначе null
      var m = /^(\d{1,2})[:.](\d{2})(?:[:.](\d{2}))?$/.exec((text || "").trim());
      return m ? (+m[1]) * 3600 + (+m[2]) * 60 + (+(m[3] || 0)) : null;
    }
    function hm(s) {
      s = ((s % 86400) + 86400) % 86400;
      var t = String(Math.floor(s / 3600)).padStart(2, "0") + ":" + String(Math.floor(s % 3600 / 60)).padStart(2, "0");
      return s % 60 ? t + ":" + String(s % 60).padStart(2, "0") : t;
    }
    var first = secs(table.dataset.first);
    var iv = (table.dataset.interval || "").trim().replace(",", ".");
    var interval = /^\d+(\.\d+)?$/.test(iv) ? Math.round(parseFloat(iv) * 60) : 0;
    function rows() { return Array.prototype.slice.call(tbody.querySelectorAll("tr[data-row]")); }
    function renumber() {
      rows().forEach(function (tr, k) {
        tr.querySelector("[data-pos]").value = k + 1;
        var t = tr.querySelector("[data-time]");
        if (!t.value.trim()) t.placeholder = first === null ? "" : hm(first + k * interval);
      });
    }
    function changed(tr) {
      renumber();
      markDirty();
      tr.classList.add("is-moved");
      setTimeout(function () { tr.classList.remove("is-moved"); }, 700);
    }
    tbody.addEventListener("input", function (e) { if (e.target.matches("[data-time]")) renumber(); });

    var drag = null;
    tbody.addEventListener("pointerdown", function (e) {
      var h = e.target.closest("[data-drag]");
      if (!h || (e.pointerType === "mouse" && e.button !== 0)) return;
      e.preventDefault();
      drag = { tr: h.closest("tr"), handle: h, moved: false };
      h.setPointerCapture(e.pointerId);
      drag.tr.classList.add("is-dragging");
    });
    tbody.addEventListener("pointermove", function (e) {
      if (!drag) return;
      var over = document.elementFromPoint(e.clientX, e.clientY);
      var tr = over && over.closest("tr[data-row]");
      if (!tr || tr === drag.tr || tr.parentNode !== tbody) return;
      var box = tr.getBoundingClientRect();
      var below = e.clientY > box.top + box.height / 2;
      tbody.insertBefore(drag.tr, below ? tr.nextSibling : tr);
      drag.moved = true;
      renumber();
    });
    function drop() {
      if (!drag) return;
      drag.tr.classList.remove("is-dragging");
      if (drag.moved) changed(drag.tr);
      drag = null;
    }
    tbody.addEventListener("pointerup", drop);
    tbody.addEventListener("pointercancel", drop);
    tbody.addEventListener("keydown", function (e) {
      var h = e.target.closest("[data-drag]");
      if (!h || (e.key !== "ArrowUp" && e.key !== "ArrowDown")) return;
      e.preventDefault();
      var tr = h.closest("tr");
      var other = e.key === "ArrowUp" ? tr.previousElementSibling : tr.nextElementSibling;
      if (!other) return;
      tbody.insertBefore(tr, e.key === "ArrowUp" ? other : other.nextSibling);
      h.focus();
      changed(tr);
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

  // --- Наглядное расписание стартов (Правки, п. 25.5): все зачёты на одной шкале времени, плашки перетаскиваются.
  var sch = document.querySelector("[data-schedule]");
  var schJson = document.getElementById("schedule-data");
  if (sch && schJson) schedule(sch, JSON.parse(schJson.textContent));

  function schedule(root, data) {
    var BRK = data["break"] || 0, pxMin = 6, open = {}, changed = false;
    var status = document.querySelector(".sch-status"), issuesBox = document.querySelector(".sch-issues");
    var lanes = data.lanes.filter(function (l) { return l.rows.length; });
    var many = data.lanes.some(function (x) { return x.cid !== data.lanes[0].cid; });  // фестиваль: несколько соревнований
    lanes.forEach(function (l) { l.label = l.title + (many ? " · " + l.comp : ""); });
    function hm(s) {
      s = ((s % 86400) + 86400) % 86400;
      var h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60);
      return (h < 10 ? "0" : "") + h + ":" + (m < 10 ? "0" : "") + m;
    }
    function retime(l) {  // время «по расчёту» — первый старт + интервал × место; вписанное вручную — как есть
      l.rows.sort(function (a, b) { return (a.t == null ? 1e9 : a.t) - (b.t == null ? 1e9 : b.t); });
      if (l.first == null) return;
      l.rows.forEach(function (r, i) { if (!r.manual) r.t = l.first + i * l.interval; });
    }
    function conflicts() {  // человек: старт + расчётное время прошлой дистанции + перерыв
      var by = {}, bad = {}, deleg = {}, texts = [];
      lanes.forEach(function (l) {
        var day = l.day ? Date.parse(l.day) / 1000 : 0;
        l.rows.forEach(function (r) {
          if (r.t == null) return;
          r.people.forEach(function (p, k) {
            (by[p] = by[p] || []).push({ at: day + r.t, l: l, r: r, fio: r.fio[k] });
          });
        });
        if (l.spread) {
          for (var i = 1; i < l.rows.length; i++) {
            if (l.rows[i].deleg === l.rows[i - 1].deleg) { deleg[l.id + "|" + l.rows[i].file] = deleg[l.id + "|" + l.rows[i - 1].file] = 1; }
          }
        }
      });
      Object.keys(by).forEach(function (p) {
        var xs = by[p].sort(function (a, b) { return a.at - b.at; });
        for (var i = 1; i < xs.length; i++) {
          var a = xs[i - 1], b = xs[i];
          if (a.l === b.l) continue;
          if (b.at - a.at < a.l.expected + BRK || b.at === a.at) {
            bad[a.l.id + "|" + a.r.file] = bad[b.l.id + "|" + b.r.file] = 1;
            texts.push(a.fio + ": " + a.l.label + " в " + hm(a.at) + " и " + b.l.label + " в " + hm(b.at) +
              " — меньше " + Math.round((a.l.expected + BRK) / 60) + " мин");
          }
        }
      });
      return { bad: bad, deleg: deleg, texts: texts };
    }
    function draw() {
      lanes.forEach(retime);
      var c = conflicts(), days = {};
      lanes.forEach(function (l) { (days[l.day || ""] = days[l.day || ""] || []).push(l); });
      root.innerHTML = "";
      Object.keys(days).sort().forEach(function (day) {
        var ls = days[day], ts = [];
        ls.forEach(function (l) { l.rows.forEach(function (r) { if (r.t != null) { ts.push(r.t); ts.push(r.t + l.dur); } }); });
        var t0 = ts.length ? Math.floor(Math.min.apply(null, ts) / 1800) * 1800 - 1800 : 0;
        var t1 = ts.length ? Math.ceil(Math.max.apply(null, ts) / 1800) * 1800 + 1800 : 3600;
        var width = (t1 - t0) / 60 * pxMin, box = document.createElement("section");
        box.className = "sch-day";
        box.innerHTML = "<h2>" + (day ? day.split("-").reverse().join(".") : "День не задан") + "</h2>";
        var scroll = document.createElement("div"), ruler = document.createElement("div");
        scroll.className = "sch-scroll"; ruler.className = "sch-ruler"; ruler.style.width = width + "px";
        for (var t = t0; t <= t1; t += 1800) {
          var tick = document.createElement("span");
          tick.style.left = ((t - t0) / 60 * pxMin) + "px"; tick.textContent = hm(t);
          ruler.appendChild(tick);
        }
        scroll.appendChild(ruler);
        ls.forEach(function (l) { lane(scroll, l, t0, width, c); });
        box.appendChild(scroll);
        root.appendChild(box);
      });
      var shown = c.texts.slice(0, 30), more = c.texts.length - shown.length;
      issuesBox.innerHTML = c.texts.length ? "<h2>Перерыв участника: " + c.texts.length + "</h2><ul>" + shown.map(function (x) {
        return "<li>" + x.replace(/[<>&]/g, "") + "</li>"; }).join("") + (more > 0 ? "<li>и ещё " + more + "</li>" : "") +
        "</ul>" : "";
    }
    function lane(scroll, l, t0, width, c) {
      var row = document.createElement("div"), label = document.createElement("a"), track = document.createElement("div");
      row.className = "sch-lane"; label.className = "sch-label"; label.href = l.url;
      label.textContent = l.label;
      track.className = "sch-track"; track.style.width = width + "px";
      row.appendChild(label); row.appendChild(track); scroll.appendChild(row);
      if (l.first == null && l.rows.every(function (r) { return r.t == null; })) {
        track.innerHTML = "<span class='sch-none'>нет времени старта — задайте его на странице жеребьёвки</span>";
        return;
      }
      if (l.block && !open[l.id]) {  // личный зачёт, связки — одним блоком
        var ts = l.rows.filter(function (r) { return r.t != null; }).map(function (r) { return r.t; });
        var a = Math.min.apply(null, ts), b = Math.max.apply(null, ts) + l.dur;
        var bl = plaque(track, l.title + ", " + hm(a) + "–" + hm(b) + ", " + l.rows.length + " " +
          (l.rows[0].people.length > 1 ? "связок" : "чел."), a, b - a, t0, "sch-block");
        if (l.rows.some(function (r) { return c.bad[l.id + "|" + r.file]; })) bl.classList.add("sch-conflict");
        drag(bl, function (d) { move(l, null, d); }, function () { open[l.id] = 1; draw(); });
        return;
      }
      if (l.block) {
        var close = document.createElement("button");
        close.type = "button"; close.className = "btn btn-ghost btn-small sch-collapse"; close.textContent = "свернуть";
        close.addEventListener("click", function () { delete open[l.id]; draw(); });
        label.after(close);
      }
      l.rows.forEach(function (r) {
        if (r.t == null) return;
        var p = plaque(track, (r.num ? r.num + " " : "") + r.name, r.t, l.dur, t0, "");
        if (r.manual) p.classList.add("sch-manual");
        if (c.bad[l.id + "|" + r.file]) p.classList.add("sch-conflict");
        if (c.deleg[l.id + "|" + r.file]) p.classList.add("sch-deleg");
        p.title = r.name + " — " + hm(r.t) + (r.fio.length ? "\n" + r.fio.join(", ") : "");
        drag(p, function (d) { move(l, r, d); }, null);
      });
    }
    function plaque(track, text, t, dur, t0, cls) {
      var p = document.createElement("button");
      p.type = "button"; p.className = "sch-item " + cls; p.textContent = text;
      p.style.left = ((t - t0) / 60 * pxMin) + "px";
      p.style.width = Math.max(dur / 60 * pxMin - 2, 18) + "px";
      track.appendChild(p);
      return p;
    }
    function move(l, r, delta) {  // delta — секунды; r — участник, иначе весь блок
      if (!delta) return;
      if (r) { r.t = Math.max(0, r.t + delta); r.manual = true; }
      else { l.first = (l.first || 0) + delta; l.rows.forEach(function (x) { if (x.manual && x.t != null) x.t += delta; }); }
      changed = true;
      if (status) status.textContent = "есть несохранённые изменения";
      draw();
    }
    function drag(el, done, click) {  // мышь, палец, клавиатура
      var x0 = null, moved = 0;
      el.addEventListener("pointerdown", function (e) {
        x0 = e.clientX; moved = 0; el.setPointerCapture(e.pointerId); el.classList.add("sch-drag");
      });
      el.addEventListener("pointermove", function (e) {
        if (x0 === null) return;
        moved = e.clientX - x0; el.style.transform = "translateX(" + moved + "px)";
      });
      el.addEventListener("pointerup", function () {
        if (x0 === null) return;
        x0 = null; el.classList.remove("sch-drag"); el.style.transform = "";
        var min = Math.round(moved / pxMin);
        if (Math.abs(moved) < 4 && click) { click(); return; }
        if (min) done(min * 60);
      });
      el.addEventListener("pointercancel", function () { x0 = null; el.style.transform = ""; });
      if (click) el.addEventListener("click", function (e) { if (e.detail === 0) click(); });  // Enter с клавиатуры
      el.addEventListener("keydown", function (e) {
        if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
        e.preventDefault();
        var id = el.textContent;
        done((e.key === "ArrowLeft" ? -60 : 60) * (e.shiftKey ? 5 : 1));
        var again = Array.prototype.filter.call(root.querySelectorAll(".sch-item"), function (x) { return x.textContent === id; })[0];
        if (again) again.focus();
      });
    }
    document.querySelectorAll("[data-sch-zoom]").forEach(function (b) {
      b.addEventListener("click", function () {
        pxMin = Math.min(30, Math.max(1, pxMin * (b.dataset.schZoom === "1" ? 1.5 : 1 / 1.5)));
        draw();
      });
    });
    var save = document.querySelector("[data-sch-save]");
    if (save) save.addEventListener("click", function () {
      var body = { lanes: lanes.map(function (l) {
        var times = {};
        l.rows.forEach(function (r) { if (r.manual && r.t != null) times[r.file] = hm(r.t); });
        return { cid: l.cid, zkey: l.zkey, order: l.rows.map(function (r) { return r.file; }), times: times,
                 first: l.first == null ? "" : hm(l.first) };
      }) };
      if (status) status.textContent = "сохраняю…";
      fetch(root.dataset.save, { method: "POST", body: JSON.stringify(body), headers: { "Content-Type": "application/json" } })
        .then(function (r) { return r.json(); })
        .then(function (j) { changed = false; if (status) status.textContent = "сохранено в " + j.saved; })
        .catch(function () { if (status) status.textContent = "не сохранилось — попробуйте ещё раз"; });
    });
    window.addEventListener("beforeunload", function (e) { if (changed) { e.preventDefault(); e.returnValue = ""; } });
    draw();
  }
})();
