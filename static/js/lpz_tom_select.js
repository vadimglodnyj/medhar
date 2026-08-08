/**
 * Tom Select для вибору зі статичного списку (ЛПЗ, спеціалізації тощо).
 * Локальний пошук; попередні/створені опції не очищаються.
 * options: selectId, listUrl, placeholder, recentKey
 */
(function () {
  const listCache = {};

  function loadNamedList(url) {
    if (!listCache[url]) {
      listCache[url] = fetch(url)
        .then(function (r) {
          if (!r.ok) throw new Error(String(r.status));
          return r.json();
        })
        .then(function (data) {
          return Array.isArray(data) ? data : data.items || [];
        })
        .catch(function () {
          delete listCache[url];
          return [];
        });
    }
    return listCache[url];
  }

  function readRecent(key) {
    if (!key) return [];
    try {
      const raw = localStorage.getItem(key);
      const arr = raw ? JSON.parse(raw) : [];
      return Array.isArray(arr) ? arr.filter(Boolean) : [];
    } catch (_) {
      return [];
    }
  }

  function pushRecent(key, value) {
    if (!key || !value) return;
    const next = [value].concat(readRecent(key).filter(function (x) {
      return String(x).toLowerCase() !== String(value).toLowerCase();
    })).slice(0, 30);
    try {
      localStorage.setItem(key, JSON.stringify(next));
    } catch (_) {}
  }

  window.initLpzTomSelect = function initLpzTomSelect(options) {
    const opts = options || {};
    const el = document.getElementById(opts.selectId);
    if (!el || typeof TomSelect === "undefined") return Promise.resolve(null);

    const listUrl = opts.listUrl || "/api/lpz_list";
    const recentKey = opts.recentKey || "lpz_recent";
    const placeholder = opts.placeholder || "Почніть вводити...";

    return loadNamedList(listUrl).then(function (names) {
      const recent = readRecent(recentKey);
      const seen = {};
      const optionsList = [];

      function addName(name) {
        const v = String(name || "").trim();
        if (!v) return;
        const k = v.toLowerCase();
        if (seen[k]) return;
        seen[k] = true;
        optionsList.push({ value: v, label: v });
      }

      recent.forEach(addName);
      names.forEach(addName);

      if (el.value) addName(el.value);
      Array.from(el.options || []).forEach(function (o) {
        if (o.value) addName(o.value);
      });

      const ts = new TomSelect(el, {
        options: optionsList,
        valueField: "value",
        labelField: "label",
        searchField: ["label"],
        maxItems: 1,
        maxOptions: 80,
        create: function (input) {
          const v = (input || "").trim();
          return { value: v, label: v };
        },
        createOnBlur: true,
        persist: true,
        preload: false,
        openOnFocus: true,
        placeholder: placeholder,
        plugins: ["clear_button"],
        render: {
          option: function (data, escape) {
            return "<div>" + escape(data.label || data.value || "") + "</div>";
          },
          item: function (data, escape) {
            return "<div>" + escape(data.value || data.label || "") + "</div>";
          },
          option_create: function (data, escape) {
            return (
              '<div class="create">Додати «' + escape(data.input) + "»</div>"
            );
          },
          no_results: function () {
            return '<div class="no-results">Нічого не знайдено</div>';
          },
        },
        onItemAdd: function (value) {
          pushRecent(recentKey, value);
        },
      });

      return ts;
    });
  };
})();
