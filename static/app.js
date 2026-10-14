/* Pasig DASRS — UI behaviour (no framework). */
(function () {
  "use strict";

  var root = document.documentElement;
  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var store = {
    get: function (k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set: function (k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } }
  };

  /* ---------------------------------------------------------------- shell */

  function initShell() {
    document.addEventListener("click", function (e) {
      var t = e.target.closest("[data-action]");
      var action = t && t.getAttribute("data-action");

      if (action === "toggle-sidebar") {
        var collapsed = root.classList.toggle("sb-collapsed");
        store.set("dasrs:sidebar", collapsed ? "collapsed" : "expanded");
        t.setAttribute("aria-label", collapsed ? "Expand menu" : "Collapse menu");
      } else if (action === "open-nav") {
        root.classList.add("nav-open");
      } else if (action === "close-nav") {
        root.classList.remove("nav-open");
      } else if (action === "toggle-menu") {
        var menu = t.closest("[data-menu]");
        var open = menu.classList.toggle("is-open");
        t.setAttribute("aria-expanded", open ? "true" : "false");
      } else if (action === "toggle-password") {
        var input = t.parentElement.querySelector("input");
        var show = input.type === "password";
        input.type = show ? "text" : "password";
        t.setAttribute("aria-label", show ? "Hide password" : "Show password");
        t.querySelector("i").className = "fa-solid fa-" + (show ? "eye-slash" : "eye");
        input.focus();
      }

      // close the user menu when clicking elsewhere
      document.querySelectorAll("[data-menu].is-open").forEach(function (m) {
        if (!m.contains(e.target)) {
          m.classList.remove("is-open");
          m.querySelector("[aria-expanded]").setAttribute("aria-expanded", "false");
        }
      });

      // clickable table rows
      var row = e.target.closest("tr[data-href]");
      if (row && !e.target.closest("a, button, form")) window.location = row.getAttribute("data-href");
    });

    document.addEventListener("keydown", function (e) {
      if (e.key !== "Escape") return;
      root.classList.remove("nav-open");
      document.querySelectorAll("[data-menu].is-open").forEach(function (m) { m.classList.remove("is-open"); });
    });

    var ticking = false;
    window.addEventListener("scroll", function () {
      if (ticking) return;
      ticking = true;
      requestAnimationFrame(function () {
        root.classList.toggle("is-scrolled", window.scrollY > 4);
        ticking = false;
      });
    }, { passive: true });
  }

  /* ---------------------------------------------------------------- toasts */

  function dismissToast(toast) {
    if (toast.classList.contains("is-leaving")) return;
    toast.classList.add("is-leaving");
    toast.addEventListener("animationend", function () { toast.remove(); }, { once: true });
  }

  function initToasts() {
    document.querySelectorAll(".toast").forEach(function (toast, i) {
      var life = toast.classList.contains("toast-bad") ? 8000 : 5000;
      var timer = toast.querySelector(".toast-timer");
      toast.style.setProperty("--life", life + "ms");
      toast.style.animationDelay = (i * 80) + "ms";
      toast.querySelector(".toast-close").addEventListener("click", function () { dismissToast(toast); });
      if (timer) timer.addEventListener("animationend", function () { dismissToast(toast); });
    });
  }

  /* ---------------------------------------------------------------- forms */

  function setLoading(form, submitter) {
    var btn = submitter && submitter.tagName === "BUTTON"
      ? submitter : form.querySelector('button[type="submit"], button:not([type])');
    root.classList.add("is-navigating");
    if (btn) {
      btn.classList.add("is-loading");
      btn.setAttribute("aria-busy", "true");
    }
    form.dataset.submitting = "1";
  }

  var dialog, pendingForm, pendingSubmitter;

  function confirmSource(form, submitter) {
    return submitter && submitter.dataset && submitter.dataset.confirm ? submitter : form;
  }

  function openConfirm(form, submitter) {
    var src = confirmSource(form, submitter);
    dialog = dialog || document.getElementById("confirm-dialog");
    if (!dialog || typeof dialog.showModal !== "function") {
      return window.confirm(src.dataset.confirm) && submitConfirmed(form, null, submitter);
    }
    pendingForm = form;
    pendingSubmitter = submitter;
    var danger = src.dataset.confirmTone === "danger";
    var ok = dialog.querySelector(".modal-ok");
    dialog.classList.toggle("is-danger", danger);
    dialog.querySelector(".modal-icon i").className =
      "fa-solid fa-" + (danger ? "triangle-exclamation" : "circle-question");
    dialog.querySelector(".modal-title").textContent = src.dataset.confirmTitle || "Please confirm";
    dialog.querySelector(".modal-text").textContent = src.dataset.confirm;
    ok.textContent = src.dataset.confirmLabel || "Confirm";
    ok.className = "btn modal-ok " + (danger ? "btn-danger" : "btn-primary");

    var field = dialog.querySelector(".modal-field");
    var inputName = src.dataset.confirmInput;
    field.hidden = !inputName;
    var old = field.querySelector("textarea, input");
    var input = old;
    if (inputName) {
      // password resets get a masked single-line input, everything else a textarea
      var wantType = src.dataset.confirmInputType === "password" ? "input" : "textarea";
      if (old.tagName.toLowerCase() !== wantType) {
        input = document.createElement(wantType);
        if (wantType === "input") { input.type = "password"; input.className = "input"; input.minLength = 6; input.autocomplete = "new-password"; }
        else { input.rows = 3; input.maxLength = 255; }
        old.replaceWith(input);
      }
      input.value = "";
      input.required = wantType === "input";
      field.querySelector(".field-label").textContent = src.dataset.confirmInputLabel || "Note";
    }
    dialog.showModal();
    if (inputName) input.focus(); else ok.focus();
  }

  function submitConfirmed(form, value, submitter) {
    var src = confirmSource(form, submitter);
    if (src.dataset.confirmInput && value !== null) {
      form.elements[src.dataset.confirmInput].value = value;
    }
    // form.submit() drops the clicked button's name/value, so carry it in a hidden field
    if (submitter && submitter.name) {
      var carry = form.querySelector('input[type="hidden"][data-carry]') || document.createElement("input");
      carry.type = "hidden";
      carry.setAttribute("data-carry", "");
      carry.name = submitter.name;
      carry.value = submitter.value;
      form.appendChild(carry);
    }
    form.dataset.confirmed = "1";
    setLoading(form, submitter);
    form.submit();
    return true;
  }

  function initForms() {
    document.addEventListener("submit", function (e) {
      var form = e.target;
      if (form.method === "dialog") return;
      if (form.dataset.submitting) { e.preventDefault(); return; } // no double submits
      if (form.hasAttribute("data-validate") && !validate(form)) { e.preventDefault(); return; }
      if (confirmSource(form, e.submitter).dataset.confirm && !form.dataset.confirmed) {
        e.preventDefault();
        openConfirm(form, e.submitter);
        return;
      }
      if (form.method.toLowerCase() === "post") setLoading(form, e.submitter);
      else root.classList.add("is-navigating");
    });

    var dlg = document.getElementById("confirm-dialog");
    if (dlg) {
      dlg.addEventListener("submit", function (e) {
        var submitter = e.submitter;
        var input = dlg.querySelector(".modal-field textarea, .modal-field input");
        if (submitter && submitter.value === "ok" && !dlg.querySelector(".modal-field").hidden
            && input.required && !input.reportValidity()) {
          e.preventDefault();
        }
      });
      dlg.addEventListener("close", function () {
        var form = pendingForm, submitter = pendingSubmitter;
        pendingForm = pendingSubmitter = null;
        if (form && dlg.returnValue === "ok") {
          var input = dlg.querySelector(".modal-field textarea, .modal-field input");
          submitConfirmed(form, dlg.querySelector(".modal-field").hidden ? null : input.value, submitter);
        }
      });
      dlg.addEventListener("cancel", function (e) {
        e.preventDefault();
        closeDialog(dlg);
      });
      dlg.querySelector('button[value="cancel"]').addEventListener("click", function (e) {
        e.preventDefault();
        closeDialog(dlg);
      });
      dlg.addEventListener("click", function (e) { if (e.target === dlg) closeDialog(dlg); });
    }

    document.querySelectorAll("[data-autosubmit]").forEach(function (el) {
      el.addEventListener("change", function () {
        root.classList.add("is-navigating");
        el.form.submit();
      });
    });

    // quantity max follows the selected medicine's stock
    document.querySelectorAll("[data-max-from]").forEach(function (input) {
      var select = input.form.elements[input.dataset.maxFrom];
      var sync = function () {
        var opt = select.options[select.selectedIndex];
        if (opt && opt.dataset.stock) input.max = opt.dataset.stock;
      };
      select.addEventListener("change", sync);
      sync();
    });
  }

  /* inline validation (forms with data-validate + novalidate) */
  function fieldFor(input) { return input.closest(".field"); }

  function setError(input, message) {
    var field = fieldFor(input);
    if (!field) return;
    field.classList.toggle("is-invalid", !!message);
    var out = field.querySelector(".field-error");
    if (out) out.textContent = message || "";
    input.setAttribute("aria-invalid", message ? "true" : "false");
  }

  function validate(form) {
    if (form.hasAttribute("data-requisition")) return validateRequisition(form);
    var first = null;
    form.querySelectorAll("input[required], select[required], textarea[required]").forEach(function (input) {
      var value = String(input.value), msg = "";
      var match = input.dataset.match && form.elements[input.dataset.match];
      if (!value.trim()) msg = input.dataset.error || "This field is required.";
      else if (input.minLength > 0 && value.length < input.minLength) msg = "Use at least " + input.minLength + " characters.";
      else if (match && value !== match.value) msg = "The passwords don't match.";
      setError(input, msg);
      if (msg && !first) first = input;
    });
    if (first) first.focus();
    return !first;
  }

  function validateRequisition(form) {
    var filled = 0, bad = null;
    form.querySelectorAll("[data-line]").forEach(function (line) {
      var sel = line.querySelector("select"), qty = line.querySelector("input");
      line.classList.remove("is-invalid");
      if (!sel.value && !qty.value) return;
      if (!sel.value || !(parseInt(qty.value, 10) > 0)) {
        line.classList.add("is-invalid");
        bad = bad || (sel.value ? qty : sel);
      } else {
        filled++;
      }
    });
    var err = form.querySelector("[data-req-error]");
    err.textContent = bad ? "Choose a medicine and enter a quantity on every line."
      : (filled ? "" : "Add at least one medicine to the request.");
    if (bad) bad.focus();
    else if (!filled) form.querySelector("[data-line] select").focus();
    return !bad && filled > 0;
  }

  function passwordStrength(value) {
    var score = 0;
    if (value.length >= 8) score++;
    if (value.length >= 12) score++;
    if (/[a-z]/.test(value) && /[A-Z]/.test(value)) score++;
    if (/\d/.test(value)) score++;
    if (/[^A-Za-z0-9]/.test(value)) score++;
    if (value.length < 8) score = Math.min(score, 1);
    return [
      ["Too short", "var(--bad-500)"], ["Weak", "var(--bad-500)"], ["Fair", "var(--warn-500)"],
      ["Good", "var(--info-500)"], ["Strong", "var(--good-500)"], ["Strong", "var(--good-500)"]
    ][score].concat(score);
  }

  function initValidation() {
    document.querySelectorAll("form[data-validate]").forEach(function (form) {
      form.addEventListener("input", function (e) {
        if (e.target.matches("[required]") && String(e.target.value).trim()) setError(e.target, "");
      });
    });

    // password strength meter
    document.querySelectorAll("[data-strength]").forEach(function (input) {
      var meter = input.closest(".field").querySelector("[data-strength-meter]");
      input.addEventListener("input", function () {
        meter.hidden = !input.value;
        var s = passwordStrength(input.value);
        meter.style.setProperty("--s", Math.max(s[2], 1) * 20 + "%");
        meter.style.setProperty("--c", s[1]);
        meter.querySelector("small").textContent = s[0];
      });
    });

    // Caps Lock warning on password fields
    var hint = document.getElementById("caps-hint");
    if (hint) {
      var caps = function (e) {
        if (e.getModifierState) hint.hidden = !e.getModifierState("CapsLock");
      };
      document.querySelectorAll('input[type="password"]').forEach(function (input) {
        input.addEventListener("keydown", caps);
        input.addEventListener("keyup", caps);
        input.addEventListener("blur", function () { hint.hidden = true; });
      });
    }

    // demo accounts: fill the form and sign in
    document.querySelectorAll("[data-demo-username]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var form = document.getElementById("loginForm");
        if (!form || form.dataset.submitting) return;
        form.elements.username.value = btn.dataset.demoUsername;
        form.elements.password.value = btn.dataset.demoPassword;
        form.querySelectorAll("input[required]").forEach(function (i) { setError(i, ""); });
        btn.classList.add("is-picked");
        form.requestSubmit ? form.requestSubmit() : form.submit();
      });
    });
  }

  /* ---------------------------------------------------------------- requisition lines */

  function initRequisition() {
    var form = document.querySelector("[data-requisition]");
    if (!form) return;
    var lines = form.querySelector("[data-lines]");
    var tpl = document.getElementById("lineTemplate");
    var label = form.querySelector("[data-submit-label]");
    var all = function () { return Array.prototype.slice.call(lines.querySelectorAll("[data-line]")); };

    function sync() {
      var chosen = all().map(function (l) { return l.querySelector("select").value; }).filter(Boolean);
      all().forEach(function (line) {
        var sel = line.querySelector("select");
        Array.prototype.forEach.call(sel.options, function (o) {
          if (!o.value) return;
          if (o.dataset.locked === undefined) o.dataset.locked = o.disabled ? "1" : "0";  // pending requests
          o.disabled = o.dataset.locked === "1" || (o.value !== sel.value && chosen.indexOf(o.value) !== -1);
        });
        var opt = sel.options[sel.selectedIndex];
        line.querySelector("[data-line-hint]").textContent = sel.value
          ? "You have " + Number(opt.dataset.stock || 0).toLocaleString() + " in stock" : "";
      });
      label.textContent = chosen.length > 1 ? "Submit request · " + chosen.length + " items" : "Submit request";
    }

    function flash(line) {
      line.classList.remove("is-flash");
      void line.offsetWidth;
      line.classList.add("is-flash");
      setTimeout(function () { line.classList.remove("is-flash"); }, 900);
    }

    function addLine(medicine, qty) {
      var line = all().filter(function (l) { return l.querySelector("select").value === String(medicine || "x"); })[0]
        || all().filter(function (l) { return !l.querySelector("select").value && !l.querySelector("input").value; })[0];
      if (!line) {
        lines.appendChild(tpl.content.cloneNode(true));
        line = all()[all().length - 1];
      }
      if (medicine) line.querySelector("select").value = medicine;
      if (qty) line.querySelector("input").value = qty;
      line.classList.remove("is-invalid");
      sync();
      flash(line);
      return line;
    }

    form.addEventListener("click", function (e) {
      var btn = e.target.closest("[data-action]");
      if (!btn) return;
      if (btn.dataset.action === "add-line") {
        var line = addLine();
        line.querySelector("select").focus();
      } else if (btn.dataset.action === "remove-line") {
        var row = btn.closest("[data-line]");
        if (all().length === 1) {
          row.querySelector("select").value = "";
          row.querySelector("input").value = "";
          sync();
          return;
        }
        row.classList.add("is-leaving");
        setTimeout(function () { row.remove(); sync(); }, 170);
      }
    });
    form.addEventListener("change", sync);

    document.querySelectorAll("[data-suggest]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        addLine(btn.dataset.medicine, btn.dataset.qty);
        btn.classList.add("is-added");
      });
    });
    var addAll = document.querySelector('[data-action="add-all-suggestions"]');
    if (addAll) {
      addAll.addEventListener("click", function () {
        document.querySelectorAll("[data-suggest]").forEach(function (btn) { btn.click(); });
      });
    }
    sync();
  }

  /* ---------------------------------------------------------------- bulk selection */

  function initBulk() {
    var bar = document.querySelector("[data-bulkbar]");
    if (!bar) return;
    var selectAll = document.querySelector("[data-select-all]");
    var boxes = function () {
      return Array.prototype.slice.call(document.querySelectorAll('input[name="ids"][form="' + bar.id + '"]'));
    };
    var last = null;

    function update() {
      var list = boxes(), n = 0;
      list.forEach(function (b) {
        b.closest("tr").classList.toggle("is-selected", b.checked);
        if (b.checked) n++;
      });
      bar.querySelector("[data-selected-count]").textContent = n;
      bar.classList.toggle("is-visible", n > 0);
      if (selectAll) {
        selectAll.checked = n > 0 && n === list.length;
        selectAll.indeterminate = n > 0 && n < list.length;
      }
    }

    document.addEventListener("click", function (e) {
      var box = e.target;
      if (box.name !== "ids" || box.form !== bar) return;
      var list = boxes(), i = list.indexOf(box);
      if (e.shiftKey && last !== null) {   // shift-click selects a range
        var a = Math.min(i, last), b = Math.max(i, last);
        for (var k = a; k <= b; k++) list[k].checked = box.checked;
      }
      last = i;
      update();
    });
    if (selectAll) {
      selectAll.addEventListener("change", function () {
        boxes().forEach(function (b) { b.checked = selectAll.checked; });
        update();
      });
    }
    bar.addEventListener("click", function (e) {
      var btn = e.target.closest("[data-action]");
      if (!btn) return;
      if (btn.dataset.action === "clear-selection") {
        boxes().forEach(function (b) { b.checked = false; });
        update();
      } else if (btn.dataset.action === "print-selected") {
        var ids = boxes().filter(function (b) { return b.checked; }).map(function (b) { return b.value; });
        window.open(btn.dataset.url + "?ids=" + ids.join(","), "_blank", "noopener");
      }
    });
    update();
  }

  /* ---------------------------------------------------------------- notifications */

  function initNotifications() {
    var box = document.querySelector("[data-notif]");
    if (!box) return;
    var list = box.querySelector("[data-notif-list]");
    var token = document.querySelector('meta[name="csrf-token"]');
    var loaded = false;

    box.querySelector(".notif-btn").addEventListener("click", function () {
      if (box.classList.contains("is-open") || loaded) return;  // closing, or already fetched
      loaded = true;
      fetch(box.dataset.feed, { credentials: "same-origin" })
        .then(function (r) { if (!r.ok) throw new Error(r.status); return r.text(); })
        .then(function (html) {
          list.innerHTML = html;
          var badge = box.querySelector(".notif-count");
          if (!badge) return;
          fetch(box.dataset.seen, {
            method: "POST",
            credentials: "same-origin",
            headers: { "X-CSRF-Token": token ? token.content : "" }
          }).then(function (r) { if (r.ok) badge.remove(); });
        })
        .catch(function () {
          loaded = false;
          list.innerHTML = '<div class="notif-empty"><p>Could not load notifications</p><small>Please try again.</small></div>';
        });
    });
  }

  /* ---------------------------------------------------------------- navigation progress */

  function initProgress() {
    document.addEventListener("click", function (e) {
      var a = e.target.closest("a[href]");
      if (!a || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      if (a.target || a.hasAttribute("download") || a.origin !== location.origin) return;
      if (a.getAttribute("href").charAt(0) === "#" || a.href.indexOf("javascript:") === 0) return;
      if (/\.csv(\?|$)/.test(a.pathname)) return;
      root.classList.add("is-navigating");
    });
    window.addEventListener("pageshow", function () { root.classList.remove("is-navigating"); });
    // printed reports should include collapsed explanations
    window.addEventListener("beforeprint", function () {
      document.querySelectorAll("details").forEach(function (d) { d.open = true; });
    });
  }

  function closeDialog(dlg) {
    dlg.returnValue = "cancel";
    if (reduceMotion) { dlg.close("cancel"); return; }
    dlg.classList.add("is-closing");
    setTimeout(function () { dlg.classList.remove("is-closing"); dlg.close("cancel"); }, 170);
  }

  /* ---------------------------------------------------------------- client-side filters */

  function initFilters() {
    document.querySelectorAll("[data-filter]").forEach(function (scope) {
      var items = function () { return document.querySelectorAll(scope.dataset.filterItems); };
      var text = scope.querySelector("[data-filter-text]");
      var selects = scope.querySelectorAll("[data-filter-key]");
      var container = scope.closest(".card, .page") || document;
      var counters = container.querySelectorAll("[data-filter-count]");
      var emptyEl = container.querySelector("[data-filter-empty]");
      var table = document.querySelector(scope.dataset.filterItems.split(" ")[0]);

      function apply() {
        var q = text ? text.value.trim().toLowerCase() : "";
        var shown = 0;
        items().forEach(function (item) {
          var ok = !q || item.textContent.toLowerCase().indexOf(q) !== -1;
          selects.forEach(function (s) {
            if (ok && s.value && item.dataset[s.dataset.filterKey] !== s.value) ok = false;
          });
          item.hidden = !ok;
          if (ok) shown++;
        });
        counters.forEach(function (c) { c.textContent = shown; });
        if (emptyEl) emptyEl.hidden = shown !== 0;
        if (table && table.tagName === "TABLE") table.hidden = shown === 0;
      }

      if (text) text.addEventListener("input", apply);
      selects.forEach(function (s) { s.addEventListener("change", apply); });
    });
  }

  /* ---------------------------------------------------------------- count-up */

  function initCounters() {
    if (reduceMotion) return;
    document.querySelectorAll("[data-count]").forEach(function (el) {
      var target = parseFloat(el.dataset.count);
      var decimals = parseInt(el.dataset.decimals || "0", 10);
      if (!isFinite(target) || target === 0) return;
      var start = null, duration = 900;
      var fmt = function (v) {
        return v.toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
      };
      el.textContent = fmt(0);
      function step(ts) {
        if (start === null) start = ts;
        var p = Math.min((ts - start) / duration, 1);
        var eased = 1 - Math.pow(1 - p, 3);
        el.textContent = fmt(decimals ? target * eased : Math.round(target * eased));
        if (p < 1) requestAnimationFrame(step);
      }
      requestAnimationFrame(step);
    });
  }

  /* ---------------------------------------------------------------- charts */

  var C = {
    series: ["#2a78d6", "#eb6834"],   // validated categorical slots 1–2
    ink: "#4a5568",
    muted: "#7a8699",
    grid: "#edf0f5",
    axis: "#cfd6e1",
    surface: "#ffffff"
  };

  function chartDefaults() {
    if (!window.Chart || chartDefaults.done) return !!window.Chart;
    var d = Chart.defaults;
    d.font.family = getComputedStyle(document.body).fontFamily;
    d.font.size = 12;
    d.color = C.muted;
    d.borderColor = C.grid;
    d.maintainAspectRatio = false;
    d.animation.duration = reduceMotion ? 0 : 900;
    d.animation.easing = "easeOutQuart";
    d.plugins.legend.align = "start";
    d.plugins.legend.labels.usePointStyle = true;
    d.plugins.legend.labels.pointStyle = "rectRounded";
    d.plugins.legend.labels.boxWidth = 10;
    d.plugins.legend.labels.boxHeight = 10;
    d.plugins.legend.labels.padding = 16;
    d.plugins.legend.labels.color = C.ink;
    // line datasets use a gradient fill; show their solid line colour in the legend instead
    var labels = d.plugins.legend.labels.generateLabels;
    d.plugins.legend.labels.generateLabels = function (chart) {
      return labels(chart).map(function (item) {
        var ds = chart.data.datasets[item.datasetIndex];
        if (chart.config.type === "line" && ds) {
          item.fillStyle = ds.borderColor;
          item.lineDash = [];
        }
        return item;
      });
    };
    var t = d.plugins.tooltip;
    t.backgroundColor = "#0f1b2d";
    t.titleColor = "#fff";
    t.bodyColor = "#dbe3ef";
    t.padding = 10;
    t.cornerRadius = 10;
    t.titleFont = { weight: "600" };
    t.boxPadding = 4;
    t.usePointStyle = true;
    t.callbacks.label = function (ctx) {
      var v = ctx.parsed[ctx.chart.options.indexAxis === "y" ? "x" : "y"];
      if (v === null || v === undefined) return null;
      return " " + ctx.dataset.label + ": " + Number(v).toLocaleString(undefined, { maximumFractionDigits: 1 });
    };
    chartDefaults.done = true;
    return true;
  }

  // vertical hairline under the hovered index (line charts)
  var crosshair = {
    id: "crosshair",
    afterDatasetsDraw: function (chart) {
      var active = chart.tooltip && chart.tooltip.getActiveElements();
      if (!active || !active.length) return;
      var x = active[0].element.x, a = chart.chartArea, ctx = chart.ctx;
      ctx.save();
      ctx.strokeStyle = C.axis;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x, a.top);
      ctx.lineTo(x, a.bottom);
      ctx.stroke();
      ctx.restore();
    }
  };

  // light band behind the forecast window
  var forecastBand = {
    id: "forecastBand",
    beforeDatasetsDraw: function (chart, args, opts) {
      if (opts.from === undefined) return;
      var xs = chart.scales.x, a = chart.chartArea, ctx = chart.ctx;
      var left = (xs.getPixelForValue(opts.from - 1) + xs.getPixelForValue(opts.from)) / 2;
      ctx.save();
      ctx.fillStyle = "rgba(235, 104, 52, .06)";
      ctx.fillRect(left, a.top, a.right - left, a.bottom - a.top);
      ctx.fillStyle = C.muted;
      ctx.font = "600 11px " + Chart.defaults.font.family;
      ctx.textAlign = "left";
      ctx.fillText("FORECAST", left + 8, a.top + 14);
      ctx.restore();
    }
  };

  // message over a chart whose series are all zero
  var emptyNote = {
    id: "emptyNote",
    afterDraw: function (chart) {
      var hasData = chart.data.datasets.some(function (ds) {
        return ds.data.some(function (v) { return v !== null && Number(v) > 0; });
      });
      if (hasData) return;
      var a = chart.chartArea, ctx = chart.ctx;
      ctx.save();
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillStyle = C.muted;
      ctx.font = "600 13px " + Chart.defaults.font.family;
      ctx.fillText(chart.options.plugins.emptyNote.text || "No data in this period",
                   (a.left + a.right) / 2, (a.top + a.bottom) / 2);
      ctx.restore();
    }
  };

  function areaFill(color) {
    return function (ctx) {
      var a = ctx.chart.chartArea;
      if (!a) return "transparent";
      var g = ctx.chart.ctx.createLinearGradient(0, a.top, 0, a.bottom);
      g.addColorStop(0, hexAlpha(color, .16));
      g.addColorStop(1, hexAlpha(color, 0));
      return g;
    };
  }

  function hexAlpha(hex, a) {
    var n = parseInt(hex.slice(1), 16);
    return "rgba(" + (n >> 16) + "," + ((n >> 8) & 255) + "," + (n & 255) + "," + a + ")";
  }

  function axes(horizontal) {
    var value = {
      beginAtZero: true,
      border: { display: false },
      grid: { color: C.grid, drawTicks: false },
      suggestedMax: 5,
      ticks: {
        padding: 8,
        precision: 0,
        maxTicksLimit: 6,
        callback: function (v) { return v >= 1000 ? (v / 1000).toLocaleString() + "K" : v.toLocaleString(); }
      }
    };
    var category = {
      border: { color: C.axis },
      grid: { display: false },
      ticks: { padding: 6, autoSkip: true, maxRotation: 0, color: horizontal ? C.ink : C.muted }
    };
    return horizontal ? { x: value, y: category } : { x: category, y: value };
  }

  function make(id, config) {
    var el = document.getElementById(id);
    if (!el || !chartDefaults()) return null;
    return new Chart(el, config);
  }

  function lineChart(id, opts) {
    return make(id, lineConfig(opts));
  }

  function lineConfig(opts) {
    return {
      type: "line",
      data: {
        labels: opts.labels,
        datasets: opts.series.map(function (s, i) {
          var color = C.series[i];
          return {
            label: s.label,
            data: s.data,
            borderColor: color,
            backgroundColor: s.area ? areaFill(color) : color,
            fill: !!s.area,
            borderWidth: 2,
            borderCapStyle: "round",
            borderJoinStyle: "round",
            tension: .35,
            pointRadius: 0,
            pointHoverRadius: 5,
            pointHoverBorderWidth: 2,
            pointHoverBorderColor: C.surface,
            pointHoverBackgroundColor: color
          };
        })
      },
      options: {
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { display: opts.series.length > 1 },
          emptyNote: { text: opts.emptyText }
        },
        scales: axes(false)
      },
      plugins: [crosshair, emptyNote]
    };
  }

  function barChart(id, opts) {
    var horizontal = !!opts.horizontal;
    return make(id, {
      type: "bar",
      data: {
        labels: opts.labels,
        datasets: opts.series.map(function (s, i) {
          return {
            label: s.label,
            data: s.data,
            backgroundColor: C.series[i],
            hoverBackgroundColor: hexAlpha(C.series[i], .85),
            borderRadius: 4,
            borderSkipped: "start",
            maxBarThickness: opts.series.length > 1 ? 12 : 22,
            categoryPercentage: .72,
            barPercentage: .86
          };
        })
      },
      options: {
        indexAxis: horizontal ? "y" : "x",
        interaction: { mode: "index", intersect: false, axis: horizontal ? "y" : "x" },
        plugins: {
          legend: { display: opts.series.length > 1 },
          emptyNote: { text: opts.emptyText }
        },
        scales: axes(horizontal)
      },
      plugins: [emptyNote]
    });
  }

  function forecastChart(id, labels, history, forecast) {
    var n = history.length;
    var hist = history.concat(forecast.map(function () { return null; }));
    var fc = history.map(function () { return null; });
    fc[n - 1] = history[n - 1];
    fc = fc.concat(forecast);
    var config = lineConfig({
      labels: labels,
      series: [
        { label: "Weekly demand", data: hist, area: true },
        { label: "Forecast", data: fc }
      ]
    });
    var f = config.data.datasets[1];
    f.borderDash = [6, 4];
    f.pointRadius = function (ctx) { return ctx.dataIndex >= n ? 4 : 0; };
    f.pointBackgroundColor = C.series[1];
    f.pointBorderColor = C.surface;
    f.pointBorderWidth = 2;
    config.options.plugins.forecastBand = { from: n };
    config.plugins.push(forecastBand);
    return make(id, config);
  }

  window.DASRS = { lineChart: lineChart, barChart: barChart, forecastChart: forecastChart };

  /* ---------------------------------------------------------------- boot */

  document.addEventListener("DOMContentLoaded", function () {
    initShell();
    initToasts();
    initForms();
    initValidation();
    initRequisition();
    initBulk();
    initNotifications();
    initProgress();
    initFilters();
    initCounters();
    var collapse = document.querySelector(".collapse-btn");
    if (collapse && root.classList.contains("sb-collapsed")) collapse.setAttribute("aria-label", "Expand menu");
  });
})();
