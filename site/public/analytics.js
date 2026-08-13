(function () {
  "use strict";

  window.plausible = window.plausible || function () {
    (window.plausible.q = window.plausible.q || []).push(arguments);
  };
  document.addEventListener("click", function (event) {
    var target = event.target && event.target.closest && event.target.closest("[data-analytics-event]");
    if (target) window.plausible(target.dataset.analyticsEvent);
  });
})();
