/**
 * Уніфікований Tom Select для звичайних <select> і текстових <input>.
 *
 * initSelectTomSelect(id|el, { create, clearButton, placeholder, onChange, allowEmptyOption })
 * initInputTomSelect(id|el, { clearButton, placeholder, locked })
 * setTomValue(el, value) / getTomValue(el)
 * refreshSelectOptions(el, [{value,label}], selectedValue)
 */
(function () {
  function resolveEl(idOrEl) {
    if (!idOrEl) return null;
    if (typeof idOrEl === "string") return document.getElementById(idOrEl);
    return idOrEl;
  }

  function baseRender() {
    return {
      option: function (data, escape) {
        return (
          "<div>" +
          escape(data.text || data.label || data.value || "") +
          "</div>"
        );
      },
      item: function (data, escape) {
        return (
          "<div>" +
          escape(data.text || data.label || data.value || "") +
          "</div>"
        );
      },
      option_create: function (data, escape) {
        return (
          '<div class="create">Використати «' +
          escape(data.input) +
          "»</div>"
        );
      },
      no_results: function () {
        return '<div class="no-results">Нічого не знайдено</div>';
      },
    };
  }

  window.setTomVisible = function setTomVisible(idOrEl, visible) {
    const el = resolveEl(idOrEl);
    if (!el) return;
    const target = el.tomselect ? el.tomselect.wrapper : el;
    target.style.display = visible ? "" : "none";
  };

  window.getTomValue = function getTomValue(idOrEl) {
    const el = resolveEl(idOrEl);
    if (!el) return "";
    if (el.tomselect) return el.tomselect.getValue() || "";
    return el.value || "";
  };

  window.setTomValue = function setTomValue(idOrEl, value) {
    const el = resolveEl(idOrEl);
    if (!el) return;
    const v = value == null ? "" : String(value);
    if (el.tomselect) {
      const ts = el.tomselect;
      const wasLocked = !!ts.isLocked;
      if (wasLocked) ts.unlock();
      if (v && !ts.options[v]) {
        ts.addOption({ value: v, text: v, label: v });
      }
      if (v) ts.setValue(v, true);
      else ts.clear(true);
      if (wasLocked) ts.lock();
      return;
    }
    el.value = v;
    if (el.tagName === "SELECT" && v && el.value !== v) {
      const opt = document.createElement("option");
      opt.value = v;
      opt.textContent = v;
      el.appendChild(opt);
      el.value = v;
    }
  };

  window.flushTomInputs = function flushTomInputs(root) {
    const rootEl =
      typeof root === "string" ? document.querySelector(root) : root || document;
    if (!rootEl) return;
    rootEl.querySelectorAll("input, select").forEach(function (el) {
      if (!el.tomselect) return;
      const ts = el.tomselect;
      const typed = String(
        (ts.control_input && ts.control_input.value) || ""
      ).trim();
      if (!typed) return;
      const current = ts.getValue();
      if (current && String(current) === typed) return;
      if (!ts.options[typed]) {
        ts.addOption({ value: typed, text: typed, label: typed });
      }
      ts.setValue(typed, true);
    });
  };

  window.refreshSelectOptions = function refreshSelectOptions(
    idOrEl,
    options,
    selectedValue
  ) {
    const el = resolveEl(idOrEl);
    if (!el) return;
    const list = options || [];
    const preferred = selectedValue == null ? "" : String(selectedValue);

    if (el.tomselect) {
      const ts = el.tomselect;
      const current = ts.getValue() || "";
      ts.clear(true);
      ts.clearOptions();
      list.forEach(function (opt) {
        const value = String(opt.value == null ? "" : opt.value);
        const label = String(opt.label == null ? value : opt.label);
        ts.addOption({ value: value, text: label, label: label });
      });
      const values = list.map(function (o) {
        return String(o.value == null ? "" : o.value);
      });
      let next = "";
      if (preferred && values.indexOf(preferred) !== -1) next = preferred;
      else if (current && values.indexOf(current) !== -1) next = current;
      if (next) ts.setValue(next, true);
      return;
    }

    const current = el.value;
    el.innerHTML = "";
    list.forEach(function (opt) {
      const option = document.createElement("option");
      option.value = opt.value == null ? "" : String(opt.value);
      option.textContent = opt.label == null ? option.value : String(opt.label);
      el.appendChild(option);
    });
    const values = Array.from(el.options).map(function (o) {
      return o.value;
    });
    if (preferred && values.indexOf(preferred) !== -1) el.value = preferred;
    else if (current && values.indexOf(current) !== -1) el.value = current;
  };

  window.initSelectTomSelect = function initSelectTomSelect(idOrEl, options) {
    const opts = options || {};
    const el = resolveEl(idOrEl);
    if (!el || typeof TomSelect === "undefined") return null;
    if (el.tomselect) return el.tomselect;

    const plugins = [];
    // clear_button лише якщо явно увімкнено (для звичайних селектів зайвий)
    if (opts.clearButton === true) plugins.push("clear_button");

    const hasEmptyOption = Array.from(el.options || []).some(function (o) {
      return o.value === "";
    });

    const ts = new TomSelect(el, {
      valueField: "value",
      labelField: "text",
      searchField: ["text"],
      maxItems: 1,
      maxOptions: null,
      allowEmptyOption:
        opts.allowEmptyOption != null
          ? !!opts.allowEmptyOption
          : hasEmptyOption,
      // Звичайний select без автокомпліту — без рядка пошуку в контролі
      controlInput: opts.searchable === true ? undefined : null,
      create: opts.create
        ? function (input) {
            const v = (input || "").trim();
            return v ? { value: v, text: v } : false;
          }
        : false,
      createOnBlur: !!opts.create,
      persist: !!opts.create,
      placeholder: opts.placeholder || el.getAttribute("placeholder") || "",
      plugins: plugins,
      hideSelected: false,
      closeAfterSelect: true,
      render: baseRender(),
      onChange: function (value) {
        if (typeof opts.onChange === "function") opts.onChange(value, this);
      },
    });

    if (opts.locked) ts.lock();
    return ts;
  };

  window.initInputTomSelect = function initInputTomSelect(idOrEl, options) {
    const opts = options || {};
    const el = resolveEl(idOrEl);
    if (!el || typeof TomSelect === "undefined") return null;
    if (el.tomselect) return el.tomselect;

    const initial = (el.value || "").trim();
    const plugins = [];
    if (opts.clearButton !== false && !opts.locked) plugins.push("clear_button");

    const tsOpts = {
      maxItems: 1,
      create: function (input) {
        const v = (input || "").trim();
        return v ? { value: v, text: v, label: v } : false;
      },
      createOnBlur: true,
      persist: false,
      hideSelected: true,
      openOnFocus: false,
      placeholder: opts.placeholder || el.getAttribute("placeholder") || "",
      plugins: plugins,
      render: baseRender(),
      onChange: function (value) {
        if (typeof opts.onChange === "function") opts.onChange(value, this);
      },
    };

    // Для чистого текстового поля — ховаємо dropdown (ввід через createOnBlur)
    if (opts.dropdown === false) {
      tsOpts.dropdownClass = "ts-dropdown d-none";
    }

    const ts = new TomSelect(el, tsOpts);

    if (initial) {
      ts.addOption({ value: initial, text: initial, label: initial });
      ts.setValue(initial, true);
    }

    if (opts.locked) {
      ts.lock();
    }

    return ts;
  };

  /**
   * Ініціалізує всі select/input у контейнері (крім уже підключених і винятків).
   * options.skipIds — масив id
   * options.selectCreateIds — id select, де дозволено create
   * options.lockedIds — readonly-подібні
   * options.onChangeMap — { id: fn }
   */
  window.initFormTomSelects = function initFormTomSelects(root, options) {
    const opts = options || {};
    const rootEl =
      typeof root === "string" ? document.querySelector(root) : root || document;
    if (!rootEl) return {};

    const skip = {};
    (opts.skipIds || []).forEach(function (id) {
      skip[id] = true;
    });
    const createIds = {};
    (opts.selectCreateIds || []).forEach(function (id) {
      createIds[id] = true;
    });
    const lockedIds = {};
    (opts.lockedIds || []).forEach(function (id) {
      lockedIds[id] = true;
    });
    const searchableIds = {};
    (opts.searchableIds || []).forEach(function (id) {
      searchableIds[id] = true;
    });
    const clearButtonIds = {};
    (opts.clearButtonIds || []).forEach(function (id) {
      clearButtonIds[id] = true;
    });
    const onChangeMap = opts.onChangeMap || {};
    const instances = {};

    rootEl.querySelectorAll("select").forEach(function (el) {
      if (!el.id || skip[el.id] || el.tomselect) return;
      if (el.hasAttribute("data-mh-no-tom") || el.closest(".mh-choice-chips")) {
        return;
      }
      if (el.disabled && !lockedIds[el.id]) return;
      instances[el.id] = initSelectTomSelect(el, {
        create: !!createIds[el.id],
        clearButton:
          opts.clearButton === true ||
          !!clearButtonIds[el.id],
        searchable:
          !!createIds[el.id] ||
          !!searchableIds[el.id] ||
          opts.searchable === true,
        locked: !!lockedIds[el.id],
        onChange: onChangeMap[el.id],
        placeholder: el.getAttribute("placeholder") || "",
      });
    });

    rootEl.querySelectorAll("input").forEach(function (el) {
      if (!el.id || skip[el.id] || el.tomselect) return;
      if (el.hasAttribute("data-mh-no-tom") || el.closest(".mh-choice-chips")) {
        return;
      }
      // Дати маскує static/js/date_input.js, не Tom Select.
      if (
        el.classList.contains("mh-date") ||
        el.hasAttribute("data-mh-date") ||
        el.hasAttribute("data-ua-date") ||
        el._mhDateMask
      ) {
        return;
      }
      const ph = (el.getAttribute("placeholder") || "").toLowerCase();
      if (ph.indexOf("дд.мм") !== -1 || ph.indexOf("dd.mm") !== -1) {
        return;
      }
      const type = (el.type || "text").toLowerCase();
      if (
        type === "hidden" ||
        type === "file" ||
        type === "checkbox" ||
        type === "radio" ||
        type === "submit" ||
        type === "button" ||
        type === "image" ||
        type === "reset"
      ) {
        return;
      }
      instances[el.id] = initInputTomSelect(el, {
        clearButton: opts.clearButton,
        locked: !!lockedIds[el.id] || el.readOnly,
        placeholder: el.getAttribute("placeholder") || "",
        dropdown: false,
        onChange: onChangeMap[el.id],
      });
    });

    return instances;
  };
})();
