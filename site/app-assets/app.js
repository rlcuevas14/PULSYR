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

  document.addEventListener("DOMContentLoaded", function () { protectForms(document); });
  document.addEventListener("htmx:configRequest", function (event) {
    var token = csrfToken();
    if (token) event.detail.headers["X-CSRF-Token"] = token;
  });
  document.addEventListener("htmx:afterSwap", function (event) { protectForms(event.target); });
})();
