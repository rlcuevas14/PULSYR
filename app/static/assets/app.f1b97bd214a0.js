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
        if (!field || !field.checkValidity || !field.checkValidity()) return;
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

  document.addEventListener("DOMContentLoaded", function () {
    protectForms(document);
    enhanceForms(document);
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
    if (target && target.matches && target.matches("[data-modal]:not(.hidden)")) {
      window.closeModal(target.id);
    }
  });
})();
