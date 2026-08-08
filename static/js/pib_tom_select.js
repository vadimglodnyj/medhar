/**
 * Tom Select для пошуку ПІБ.
 * options:
 *   selectId, placeholder, context ('service' | '' для treatments),
 *   allowCreate, shouldLoad(query), onSelect(item), onClear()
 */
function initPibTomSelect(options) {
  const opts = options || {};
  const el = document.getElementById(opts.selectId || "pib_nazivnyi");
  if (!el || typeof TomSelect === "undefined") return null;

  const context = opts.context == null ? "service" : opts.context;

  const ts = new TomSelect(el, {
    valueField: "value",
    labelField: "label",
    searchField: ["label", "value"],
    maxItems: 1,
    maxOptions: 10,
    create: opts.allowCreate
      ? function (input) {
          const v = (input || "").trim();
          return { value: v, label: v };
        }
      : false,
    createOnBlur: !!opts.allowCreate,
    persist: false,
    preload: false,
    openOnFocus: true,
    loadThrottle: 200,
    placeholder: opts.placeholder || "Почніть вводити ПІБ...",
    plugins: ["clear_button"],
    shouldLoad: function (query) {
      if (typeof opts.shouldLoad === "function") {
        return opts.shouldLoad(query);
      }
      return (query || "").trim().length >= 2;
    },
    // Не фільтрувати серверні результати на клієнті повторно
    score: function () {
      return function () {
        return 1;
      };
    },
    load: function (query, callback) {
      const q = (query || "").trim();
      if (q.length < 2) {
        callback();
        return;
      }
      const self = this;
      let url = `/api/search_pib?q=${encodeURIComponent(q)}`;
      if (context) {
        url += `&context=${encodeURIComponent(context)}`;
      }
      fetch(url)
        .then(function (r) {
          if (!r.ok) throw new Error(String(r.status));
          return r.json();
        })
        .then(function (data) {
          // Прибрати результати попереднього запиту, інакше вони лишаються в dropdown.
          self.clearOptions();
          callback(data.results || []);
        })
        .catch(function () {
          callback();
        });
    },
    render: {
      option: function (data, escape) {
        return "<div>" + escape(data.label || data.value || "") + "</div>";
      },
      item: function (data, escape) {
        return "<div>" + escape(data.value || data.label || "") + "</div>";
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
    },
    onItemAdd: function (value) {
      const item = this.options[value];
      if (opts.onSelect) opts.onSelect(item || { value: value });
    },
    onClear: function () {
      if (opts.onClear) opts.onClear();
    },
    onDelete: function () {
      if (opts.onClear) opts.onClear();
    },
  });

  return ts;
}
