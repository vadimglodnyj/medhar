/**
 * Маска та валідація дат у форматі дд.мм.рррр (без зовнішніх залежностей).
 *
 * formatUaDateDigits("22082026") → "22.08.2026"
 * isValidUaDate("29.02.2024") → true
 * isValidUaDate("29.02.2023") → false
 * isValidUaDate("32.01.2020") → false
 * isValidUaDate("15.13.2020") → false
 * isValidUaDate("01.01.1899") → false
 *
 * Поля: class="mh-date" або data-mh-date / data-ua-date
 * (Tom Select їх пропускає — див. form_tom_select.js).
 *
 * API: initDateInputs(root), bindUaDateInput(el), isValidUaDate, normalizeUaDate
 */
(function () {
  var MSG =
    "Введіть коректну дату: день 01–31, місяць 01–12, рік 1900–2200 (напр. 22082026 → 22.08.2026)";
  var SELECTOR =
    "input.mh-date, input[data-mh-date], input[data-ua-date], input.js-ua-date";
  var DEFAULT_MIN_YEAR = 1900;
  var DEFAULT_MAX_YEAR = 2200;

  function digitsOnly(value, maxLen) {
    var d = String(value == null ? "" : value).replace(/\D/g, "");
    if (maxLen != null) d = d.slice(0, maxLen);
    return d;
  }

  function pad2(n) {
    var s = String(n);
    return s.length === 1 ? "0" + s : s;
  }

  /**
   * Обмежує день (01–31) і місяць (01–12) уже під час введення.
   * Рік лише обрізає до 4 цифр — повну перевірку 1900–2200 робить isValidUaDate.
   */
  function sanitizeUaDateDigits(raw) {
    var d = digitsOnly(raw, 8);
    if (!d) return "";

    var day = d.slice(0, Math.min(2, d.length));
    var month = d.length > 2 ? d.slice(2, Math.min(4, d.length)) : "";
    var year = d.length > 4 ? d.slice(4, Math.min(8, d.length)) : "";

    if (day.length === 1) {
      // 4–9 → одразу 04–09
      if (parseInt(day, 10) > 3) day = "0" + day;
    } else if (day.length === 2) {
      var dayNum = parseInt(day, 10);
      if (dayNum === 0) day = "01";
      else if (dayNum > 31) day = "31";
    }

    if (month.length === 1) {
      if (parseInt(month, 10) > 1) month = "0" + month;
    } else if (month.length === 2) {
      var monthNum = parseInt(month, 10);
      if (monthNum === 0) month = "01";
      else if (monthNum > 12) month = "12";
    }

    return day + month + year;
  }

  function formatUaDateDigits(raw) {
    var d = sanitizeUaDateDigits(raw);
    var out = "";
    if (d.length > 0) out = d.slice(0, Math.min(2, d.length));
    if (d.length > 2) out += "." + d.slice(2, Math.min(4, d.length));
    if (d.length > 4) out += "." + d.slice(4, Math.min(8, d.length));
    return out;
  }

  function normalizeUaDate(raw) {
    return formatUaDateDigits(raw);
  }

  function parseUaDateParts(dateString) {
    var s = String(dateString == null ? "" : dateString).trim();
    var match = /^(\d{2})\.(\d{2})\.(\d{4})$/.exec(s);
    if (!match) return null;
    return {
      day: parseInt(match[1], 10),
      month: parseInt(match[2], 10),
      year: parseInt(match[3], 10),
    };
  }

  /**
   * Реальна календарна дата дд.мм.рррр.
   * День 1–31, місяць 1–12, рік 1900–2200 (або opts.minYear / opts.maxYear).
   */
  function isValidUaDate(dateString, opts) {
    var options = opts || {};
    var parts = parseUaDateParts(dateString);
    if (!parts) return false;
    var day = parts.day;
    var month = parts.month;
    var year = parts.year;
    var minYear =
      options.minYear != null ? options.minYear : DEFAULT_MIN_YEAR;
    var maxYear =
      options.maxYear != null ? options.maxYear : DEFAULT_MAX_YEAR;
    if (month < 1 || month > 12) return false;
    if (day < 1 || day > 31) return false;
    if (year < minYear || year > maxYear) return false;
    var date = new Date(year, month - 1, day);
    return (
      date.getFullYear() === year &&
      date.getMonth() === month - 1 &&
      date.getDate() === day
    );
  }

  function resolveEl(idOrEl) {
    if (!idOrEl) return null;
    if (typeof idOrEl === "string") return document.getElementById(idOrEl);
    return idOrEl;
  }

  function getRawValue(el) {
    if (!el) return "";
    if (typeof window.getTomValue === "function" && el.tomselect) {
      return String(window.getTomValue(el) || "");
    }
    return String(el.value || "");
  }

  function setRawValue(el, value) {
    if (!el) return;
    var v = value == null ? "" : String(value);
    if (typeof window.setTomValue === "function" && el.tomselect) {
      window.setTomValue(el, v);
      return;
    }
    el.value = v;
  }

  function typingTarget(el) {
    if (el && el.tomselect && el.tomselect.control_input) {
      return el.tomselect.control_input;
    }
    return el;
  }

  function caretPosFromDigits(formatted, digitCount) {
    if (digitCount <= 0) return 0;
    var seen = 0;
    for (var i = 0; i < formatted.length; i++) {
      if (/\d/.test(formatted.charAt(i))) {
        seen += 1;
        if (seen >= digitCount) return i + 1;
      }
    }
    return formatted.length;
  }

  function digitCountBefore(value, caret) {
    var s = String(value || "").slice(0, Math.max(0, caret));
    return digitsOnly(s).length;
  }

  function applyFormatted(el, formatted, caretDigits) {
    if (el.tomselect) {
      setRawValue(el, formatted);
      var ci = el.tomselect.control_input;
      if (ci) {
        ci.value = formatted;
        var pos = caretPosFromDigits(formatted, caretDigits);
        try {
          ci.setSelectionRange(pos, pos);
        } catch (e) {}
      }
      return;
    }
    el.value = formatted;
    var pos2 = caretPosFromDigits(formatted, caretDigits);
    try {
      el.setSelectionRange(pos2, pos2);
    } catch (e2) {}
  }

  function readYearBounds(el, opts) {
    var minYear = DEFAULT_MIN_YEAR;
    var maxYear = DEFAULT_MAX_YEAR;
    if (opts && opts.minYear != null) minYear = opts.minYear;
    if (opts && opts.maxYear != null) maxYear = opts.maxYear;

    var maxAttr =
      (el && el.getAttribute("data-ua-date-max-year")) ||
      (el && el.getAttribute("data-mh-date-max-year"));
    if (maxAttr) {
      if (maxAttr === "now" || maxAttr === "current") {
        maxYear = new Date().getFullYear();
      } else {
        var n = parseInt(maxAttr, 10);
        if (!isNaN(n)) maxYear = n;
      }
    }

    var minAttr =
      (el && el.getAttribute("data-ua-date-min-year")) ||
      (el && el.getAttribute("data-mh-date-min-year"));
    if (minAttr) {
      var m = parseInt(minAttr, 10);
      if (!isNaN(m)) minYear = m;
    }

    return { minYear: minYear, maxYear: maxYear };
  }

  function syncValidity(el, opts) {
    var v = String(getRawValue(el) || "").trim();
    if (!v) {
      el.setCustomValidity(el.required ? MSG : "");
      return !el.required;
    }
    // Неповна дата (ще друкують) — не блокуємо, поки не повні 8 цифр.
    if (digitsOnly(v).length < 8) {
      el.setCustomValidity("");
      return true;
    }
    var bounds = readYearBounds(el, opts);
    var ok = isValidUaDate(v, bounds);
    el.setCustomValidity(ok ? "" : MSG);
    return ok;
  }

  function bindUaDateInput(idOrEl, options) {
    var el = resolveEl(idOrEl);
    if (!el) return null;
    if (el.dataset.uaDateBound === "1") return el;
    el.dataset.uaDateBound = "1";
    el._mhDateMask = true;

    if (el.tomselect) {
      try {
        el.tomselect.destroy();
      } catch (e) {}
    }

    if (!el.classList.contains("mh-date")) el.classList.add("mh-date");
    if (!el.getAttribute("inputmode")) el.setAttribute("inputmode", "numeric");
    if (!el.getAttribute("autocomplete")) el.setAttribute("autocomplete", "off");
    if (!el.getAttribute("placeholder")) {
      el.setAttribute("placeholder", "дд.мм.рррр");
    }
    if (!el.getAttribute("title")) el.setAttribute("title", MSG);
    if (el.getAttribute("pattern")) el.removeAttribute("pattern");

    var opts = options || {};
    var initial = normalizeUaDate(getRawValue(el));
    if (initial) setRawValue(el, initial);

    function onInput(e) {
      var target = e.target;
      var before = target.value;
      var caret =
        target.selectionStart != null ? target.selectionStart : before.length;
      var digsBefore = digitCountBefore(before, caret);
      var formatted = normalizeUaDate(before);
      applyFormatted(el, formatted, digsBefore);
      syncValidity(el, opts);
    }

    function onPaste(e) {
      e.preventDefault();
      var text = "";
      if (e.clipboardData) text = e.clipboardData.getData("text");
      else if (window.clipboardData) text = window.clipboardData.getData("Text");
      var formatted = normalizeUaDate(text);
      applyFormatted(el, formatted, digitsOnly(formatted).length);
      syncValidity(el, opts);
    }

    function onBlur() {
      var formatted = normalizeUaDate(getRawValue(el));
      if (formatted !== getRawValue(el)) setRawValue(el, formatted);
      if (el.tomselect && el.tomselect.control_input) {
        el.tomselect.control_input.value = formatted;
      }
      // На blur неповна дата з цифрами — помилка.
      var digs = digitsOnly(formatted);
      if (digs.length > 0 && digs.length < 8) {
        el.setCustomValidity(MSG);
        return;
      }
      syncValidity(el, opts);
    }

    var target = typingTarget(el);
    target.addEventListener("input", onInput);
    target.addEventListener("paste", onPaste);
    el.addEventListener("blur", onBlur, true);
    if (target !== el) target.addEventListener("blur", onBlur);

    // Перед submit форми — перевірити дату.
    var form = el.form;
    if (form && !form._mhDateSubmitBound) {
      form._mhDateSubmitBound = true;
      form.addEventListener(
        "submit",
        function (ev) {
          var fields = form.querySelectorAll(SELECTOR);
          var firstInvalid = null;
          for (var i = 0; i < fields.length; i++) {
            var field = fields[i];
            var digs = digitsOnly(field.value || "");
            if (!digs.length && !field.required) {
              field.setCustomValidity("");
              continue;
            }
            if (digs.length > 0 && digs.length < 8) {
              field.setCustomValidity(MSG);
              if (!firstInvalid) firstInvalid = field;
              continue;
            }
            var bounds = readYearBounds(field, null);
            if (!isValidUaDate(field.value || "", bounds)) {
              field.setCustomValidity(MSG);
              if (!firstInvalid) firstInvalid = field;
            } else {
              field.setCustomValidity("");
            }
          }
          if (firstInvalid) {
            ev.preventDefault();
            firstInvalid.reportValidity();
            firstInvalid.focus();
          }
        },
        true
      );
    }

    attachNativeDatepicker(el);
    return el;
  }

  function uaDateToIso(value) {
    var parts = parseUaDateParts(value);
    if (!parts) return "";
    return parts.year + "-" + pad2(parts.month) + "-" + pad2(parts.day);
  }

  function isoToUaDate(value) {
    var match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value || "").trim());
    if (!match) return "";
    return match[3] + "." + match[2] + "." + match[1];
  }

  function attachNativeDatepicker(el) {
    if (!el || el.dataset.mhPickerBound === "1") return;
    if (el.getAttribute("data-mh-no-picker") != null) return;
    if (el.closest(".mh-date-wrap")) {
      el.dataset.mhPickerBound = "1";
      return;
    }
    el.dataset.mhPickerBound = "1";
    var wrap = document.createElement("div");
    wrap.className = "mh-date-wrap";
    if (el.style.width) {
      wrap.style.width = el.style.width;
      wrap.style.display = "inline-block";
      el.style.width = "100%";
    } else {
      wrap.style.width = "100%";
    }
    if (el.parentNode) el.parentNode.insertBefore(wrap, el);
    wrap.appendChild(el);

    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "mh-date-cal-btn";
    btn.setAttribute("tabindex", "-1");
    btn.setAttribute("aria-label", "Відкрити календар");
    btn.innerHTML = '<i class="fas fa-calendar-days" aria-hidden="true"></i>';

    var native = document.createElement("input");
    native.type = "date";
    native.className = "mh-date-native";
    native.setAttribute("tabindex", "-1");
    native.setAttribute("aria-hidden", "true");

    wrap.appendChild(btn);
    wrap.appendChild(native);

    function syncNative() {
      native.value = uaDateToIso(el.value) || "";
    }
    syncNative();
    el.addEventListener("input", syncNative);
    el.addEventListener("blur", syncNative);
    native.addEventListener("change", function () {
      if (!native.value) return;
      var ua = isoToUaDate(native.value);
      if (!ua) return;
      setRawValue(el, ua);
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
    });
    function openPicker(ev) {
      ev.preventDefault();
      syncNative();
      try {
        if (typeof native.showPicker === "function") native.showPicker();
        else native.click();
      } catch (err) {
        native.click();
      }
    }
    btn.addEventListener("click", openPicker);
    native.addEventListener("click", function (ev) {
      ev.stopPropagation();
    });
  }

  function initUaDateInputs(root) {
    var rootEl =
      typeof root === "string"
        ? document.querySelector(root)
        : root || document;
    if (!rootEl) return [];
    var nodes = rootEl.querySelectorAll(SELECTOR);
    var bound = [];
    for (var i = 0; i < nodes.length; i++) {
      bound.push(bindUaDateInput(nodes[i]));
    }
    return bound;
  }

  window.formatUaDateDigits = formatUaDateDigits;
  window.normalizeUaDate = normalizeUaDate;
  window.isValidUaDate = isValidUaDate;
  window.bindUaDateInput = bindUaDateInput;
  window.initUaDateInputs = initUaDateInputs;
  window.initDateInput = bindUaDateInput;
  window.initDateInputs = initUaDateInputs;
  window.bindMhDateInput = bindUaDateInput;
  window.initMhDateInputs = initUaDateInputs;

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      initUaDateInputs(document);
    });
  } else {
    initUaDateInputs(document);
  }
})();
