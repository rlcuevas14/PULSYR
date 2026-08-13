(function () {
  "use strict";

  try {
    var storedTheme = localStorage.getItem("pulsyr-theme");
    var useDark = storedTheme === "dark" ||
      (!storedTheme && window.matchMedia("(prefers-color-scheme: dark)").matches);
    if (useDark) document.documentElement.classList.add("dark");
    var themeMeta = document.querySelector('meta[name="theme-color"]');
    if (themeMeta && useDark) themeMeta.content = "#0e0e10";
  } catch (_) {
    // Storage can be unavailable; the light theme remains usable.
  }

  window.toggleTheme = function () {
    var dark = document.documentElement.classList.toggle("dark");
    try { localStorage.setItem("pulsyr-theme", dark ? "dark" : "light"); } catch (_) {}
    var themeMeta = document.querySelector('meta[name="theme-color"]');
    if (themeMeta) themeMeta.content = dark ? "#0e0e10" : "#fffaf0";
  };

  function csrfToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.content : "";
  }

  function protectForms(root) {
    var token = csrfToken();
    if (!token) return;
    (root || document).querySelectorAll("form").forEach(function (form) {
      var method = (form.getAttribute("method") || (form.hasAttribute("hx-post") ? "post" : "get"));
      if (method.toLowerCase() !== "post" || form.querySelector('input[name="csrf_token"]')) return;
      var input = document.createElement("input");
      input.type = "hidden";
      input.name = "csrf_token";
      input.value = token;
      form.prepend(input);
    });
  }

  function localeText() {
    var lang = (document.documentElement.lang || "en").slice(0, 2);
    return ({
      es: { review: "Revisa los campos indicados.", cancel: "Cancelar", confirm: "Confirmar" },
      fr: { review: "Vérifiez les champs indiqués.", cancel: "Annuler", confirm: "Confirmer" },
      en: { review: "Review the highlighted fields.", cancel: "Cancel", confirm: "Confirm" }
    })[lang] || { review: "Review the highlighted fields.", cancel: "Cancel", confirm: "Confirm" };
  }

  function enhanceForms(root) {
    (root || document).querySelectorAll("form:not([data-p-form-ready])").forEach(function (form) {
      form.dataset.pFormReady = "true";
      form.addEventListener("invalid", function (event) {
        var field = event.target;
        if (!field || !field.setAttribute) return;
        if (!field.id) field.id = "field-" + Math.random().toString(36).slice(2);
        var errorId = field.id + "-error";
        var error = document.getElementById(errorId);
        if (!error) {
          error = document.createElement("p");
          error.id = errorId;
          error.className = "p-field-error";
          error.setAttribute("role", "alert");
          field.insertAdjacentElement("afterend", error);
        }
        error.textContent = field.validationMessage;
        field.setAttribute("aria-invalid", "true");
        var describedBy = (field.getAttribute("aria-describedby") || "").split(/\s+/).filter(Boolean);
        if (describedBy.indexOf(errorId) < 0) describedBy.push(errorId);
        field.setAttribute("aria-describedby", describedBy.join(" "));
        window.setTimeout(function () {
          var summary = form.querySelector("[data-form-error-summary]");
          if (!summary) {
            summary = document.createElement("div");
            summary.dataset.formErrorSummary = "true";
            summary.className = "bg-error/10 border border-error/30 text-error rounded-xl px-4 py-3 text-sm";
            summary.setAttribute("role", "alert");
            summary.tabIndex = -1;
            form.prepend(summary);
          }
          summary.textContent = localeText().review;
          summary.focus();
        }, 0);
      }, true);
      form.addEventListener("input", function (event) {
        var field = event.target;
        // `checkValidity()` emits another `invalid` event when the partially
        // typed value is invalid. The handler above then focuses the summary,
        // stealing focus from the field after every keystroke. Read the
        // ValidityState instead; it reports the same state without side effects.
        if (!field || !field.validity || !field.validity.valid) return;
        field.removeAttribute("aria-invalid");
        var error = field.id && document.getElementById(field.id + "-error");
        if (error) error.remove();
      });
    });
  }

  var confirmedForms = new WeakSet();
  var confirmContinuation = null;
  function confirmationDialog() {
    var dialog = document.getElementById("p-confirm-dialog");
    if (dialog) return dialog;
    var copy = localeText();
    dialog = document.createElement("dialog");
    dialog.id = "p-confirm-dialog";
    dialog.className = "p-confirm-dialog";
    dialog.setAttribute("aria-labelledby", "p-confirm-title");
    dialog.innerHTML = '<div class="p-5"><h2 id="p-confirm-title" class="text-lg font-semibold text-ink"></h2>' +
      '<div class="flex justify-end gap-3 mt-5"><button type="button" class="p-btn p-btn-ghost" data-confirm-cancel></button>' +
      '<button type="button" class="p-btn p-btn-primary" data-confirm-accept></button></div></div>';
    dialog.querySelector("[data-confirm-cancel]").textContent = copy.cancel;
    dialog.querySelector("[data-confirm-accept]").textContent = copy.confirm;
    dialog.querySelector("[data-confirm-cancel]").addEventListener("click", function () { dialog.close(); });
    dialog.querySelector("[data-confirm-accept]").addEventListener("click", function () {
      var continuation = confirmContinuation;
      confirmContinuation = null;
      dialog.close();
      if (continuation) continuation();
    });
    dialog.addEventListener("close", function () { confirmContinuation = null; });
    document.body.appendChild(dialog);
    return dialog;
  }

  function setPending(form, pending) {
    if (!pending) delete form.dataset.submitting;
    form.setAttribute("aria-busy", pending ? "true" : "false");
    form.querySelectorAll('button[type="submit"], input[type="submit"]').forEach(function (button) {
      button.disabled = pending;
      button.classList.toggle("p-submit-pending", pending);
    });
  }

  var toastTimer = null;
  function showToast(message, kind) {
    var toast = document.getElementById("toast");
    if (!toast || !message) return;
    toast.textContent = message;
    toast.classList.remove("hidden", "bg-error", "bg-success");
    toast.classList.add(kind === "success" ? "bg-success" : "bg-error");
    if (toastTimer) window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(function () {
      toast.classList.add("hidden");
    }, kind === "success" ? 3000 : 5000);
  }
  window.showToast = showToast;

  function colorForeground(color) {
    var value = color.replace("#", "");
    var channels = [0, 2, 4].map(function (offset) {
      var channel = parseInt(value.substr(offset, 2), 16) / 255;
      return channel <= 0.04045 ? channel / 12.92 : Math.pow((channel + 0.055) / 1.055, 2.4);
    });
    return (0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]) > 0.35
      ? "#0a0a0a" : "#ffffff";
  }

  function previewColor(picker, color) {
    var input = picker.querySelector("[data-color-input]");
    var preview = picker.querySelector("[data-color-preview]");
    picker.querySelectorAll("[data-color-pick]").forEach(function (button) {
      button.style.outline = button.dataset.colorPick === color ? "2px solid var(--ink)" : "";
      button.style.outlineOffset = button.dataset.colorPick === color ? "2px" : "";
    });
    if (input) input.value = color;
    if (preview) {
      preview.style.background = color;
      preview.style.color = colorForeground(color);
    }
  }

  function editPending(data) {
    var title = document.getElementById("pending-modal-title");
    if (!title) return;
    title.textContent = data ? title.dataset.edit : title.dataset.new;
    var values = {
      "pm-id": data ? data.id : "",
      "pm-title": data ? data.title : "",
      "pm-detail": data ? data.detail : "",
      "pm-owner": data ? data.owner : "",
      "pm-status": data ? data.status : "open",
      "pm-due": data ? data.due : "",
      "pm-task": data ? data.task : ""
    };
    Object.keys(values).forEach(function (id) {
      var field = document.getElementById(id);
      if (field) field.value = values[id] || "";
    });
    window.openModal("pending-modal");
  }

  var boardInstances = [];
  function boardMove(itemId, toStatus) {
    var form = document.getElementById("filters");
    var values = { status: toStatus };
    if (form) new FormData(form).forEach(function (value, key) { values[key] = value; });
    window.htmx.ajax("POST", "/ui/items/" + itemId + "/board-move", {
      target: "#items-view", swap: "innerHTML", values: values
    });
  }

  function initBoard() {
    var root = document.getElementById("board-root");
    boardInstances.forEach(function (sortable) { sortable.destroy(); });
    boardInstances = [];
    if (!root || !root.dataset.canWrite || !window.Sortable) return;
    root.querySelectorAll("[data-board-col]").forEach(function (column) {
      boardInstances.push(window.Sortable.create(column, {
        group: "pulsyr-board",
        animation: 150,
        sort: false,
        draggable: "[data-card]",
        handle: "[data-drag-handle]",
        ghostClass: "opacity-40",
        onEnd: function (event) {
          var to = event.to.dataset.status;
          var from = event.from.dataset.status;
          if (to && to !== from) boardMove(event.item.dataset.itemId, to);
        }
      }));
    });
  }

  function initializeDeclarativeUi(root) {
    (root || document).querySelectorAll("[data-hint-id]").forEach(function (hint) {
      try {
        if (!localStorage.getItem("hint-" + hint.dataset.hintId)) {
          hint.classList.remove("hidden");
          hint.classList.add("flex");
        }
      } catch (_) {}
    });
    (root || document).querySelectorAll("[data-auto-remove]").forEach(function (element) {
      window.setTimeout(function () { if (element.isConnected) element.remove(); }, Number(element.dataset.autoRemove));
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    protectForms(document);
    enhanceForms(document);
    initializeDeclarativeUi(document);
    initBoard();
    var toast = document.getElementById("toast");
    if (toast && toast.dataset.initialMessage) showToast(toast.dataset.initialMessage, "success");
  });
  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!form || form.tagName !== "FORM") return;
    if (form.dataset.submitting === "true") {
      event.preventDefault();
      return;
    }
    var submitter = event.submitter;
    var message = (submitter && submitter.dataset.confirm) || form.dataset.confirm;
    if (message && !confirmedForms.has(form)) {
      event.preventDefault();
      var dialog = confirmationDialog();
      dialog.querySelector("#p-confirm-title").textContent = message;
      confirmContinuation = function () {
        confirmedForms.add(form);
        form.requestSubmit(submitter || undefined);
      };
      dialog.showModal();
      return;
    }
    confirmedForms.delete(form);
    form.dataset.submitting = "true";
    window.setTimeout(function () { setPending(form, true); }, 0);
  }, true);
  document.addEventListener("htmx:configRequest", function (event) {
    var token = csrfToken();
    if (token) event.detail.headers["X-CSRF-Token"] = token;
  });
  document.addEventListener("htmx:afterSwap", function (event) {
    protectForms(event.target);
    enhanceForms(event.target);
    initializeDeclarativeUi(event.target);
    if (event.target && (event.target.id === "items-view" || event.target.id === "backlog-root")) initBoard();
  });
  document.addEventListener("htmx:afterRequest", function (event) {
    var source = event.detail && event.detail.elt;
    var form = source && source.closest ? source.closest("form") : null;
    if (form) setPending(form, false);
  });

  var modalTrigger = null;
  function focusable(modal) {
    return Array.from(modal.querySelectorAll(
      'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), ' +
      'textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
    )).filter(function (element) { return !element.hidden && element.offsetParent !== null; });
  }

  window.openModal = function (id) {
    var modal = document.getElementById(id);
    if (!modal) return;
    modalTrigger = document.activeElement;
    modal.classList.remove("hidden");
    document.body.style.overflow = "hidden";
    var controls = focusable(modal);
    if (controls.length) controls[0].focus();
  };

  window.closeModal = function (id) {
    var modal = document.getElementById(id);
    if (!modal) return;
    modal.classList.add("hidden");
    document.body.style.overflow = "";
    if (modalTrigger && modalTrigger.focus) modalTrigger.focus();
    modalTrigger = null;
  };

  document.addEventListener("keydown", function (event) {
    var modal = document.querySelector("[data-modal]:not(.hidden)");
    if (!modal) return;
    if (event.key === "Escape") {
      event.preventDefault();
      window.closeModal(modal.id);
      return;
    }
    if (event.key !== "Tab") return;
    var controls = focusable(modal);
    if (!controls.length) { event.preventDefault(); return; }
    var first = controls[0];
    var last = controls[controls.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault(); last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault(); first.focus();
    }
  });

  document.addEventListener("click", function (event) {
    var target = event.target;
    var action = target && target.closest ? target.closest(
      "[data-theme-toggle], [data-modal-open], [data-modal-close], [data-remove], " +
      "[data-copy-target], [data-clear-target], [data-hint-dismiss], [data-color-pick], " +
      "[data-pending-new], [data-pending-edit]"
    ) : null;
    if (action) {
      if (action.hasAttribute("data-theme-toggle")) window.toggleTheme();
      if (action.dataset.modalOpen) window.openModal(action.dataset.modalOpen);
      if (action.dataset.modalClose) window.closeModal(action.dataset.modalClose);
      if (action.hasAttribute("data-remove")) action.remove();
      if (action.dataset.clearTarget) {
        var clearTarget = document.getElementById(action.dataset.clearTarget);
        if (clearTarget) clearTarget.replaceChildren();
      }
      if (action.dataset.copyTarget) {
        var copyTarget = document.getElementById(action.dataset.copyTarget);
        var copyText = copyTarget && (copyTarget.value || copyTarget.textContent || "");
        navigator.clipboard.writeText(copyText).then(function () {
          action.textContent = action.dataset.copied;
          window.setTimeout(function () { action.textContent = action.dataset.copy; }, 1500);
        });
      }
      if (action.hasAttribute("data-hint-dismiss")) {
        var hint = action.closest("[data-hint-id]");
        if (hint) {
          try { localStorage.setItem("hint-" + hint.dataset.hintId, "1"); } catch (_) {}
          hint.remove();
        }
      }
      if (action.dataset.colorPick) previewColor(action.closest("[data-color-picker]"), action.dataset.colorPick);
      if (action.hasAttribute("data-pending-new")) editPending(null);
      if (action.dataset.pendingEdit) editPending(JSON.parse(action.dataset.pendingEdit));
    }
    if (target && target.matches && target.matches("[data-modal]:not(.hidden)")) {
      window.closeModal(target.id);
    }
    document.querySelectorAll("details.p-menu[open]").forEach(function (details) {
      if (!details.contains(target)) details.removeAttribute("open");
    });
  });

  document.addEventListener("change", function (event) {
    var target = event.target;
    if (target && target.matches("[data-auto-submit]") && target.form) target.form.requestSubmit();
    if (target && target.matches("[data-color-input]")) previewColor(target.closest("[data-color-picker]"), target.value);
  });

  document.addEventListener("htmx:responseError", function (event) {
    var xhr = event.detail && event.detail.xhr;
    var body = xhr && xhr.responseText ? xhr.responseText.trim() : "";
    if (body && body.charAt(0) === "<") {
      var temporary = document.createElement("div");
      temporary.innerHTML = body;
      body = (temporary.textContent || "").trim();
    }
    var toast = document.getElementById("toast");
    var status = xhr ? xhr.status : "";
    var generic = toast ? toast.dataset.errorGeneric : "Request failed__S__";
    showToast(body || generic.replace("__S__", status ? " (" + status + ")" : ""));
  });
  document.addEventListener("htmx:sendError", function () {
    var toast = document.getElementById("toast");
    showToast(toast ? toast.dataset.offline : "Offline");
  });
  document.addEventListener("pulsyr:toast", function (event) {
    var detail = event.detail || {};
    showToast(detail.message || "", detail.kind || "error");
  });
})();
