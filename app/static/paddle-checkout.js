// Reads its configuration from the script tag's data attributes: the app's CSP
// has no inline-script exception and this file is not the place to add one.
(function () {
  var el = document.currentScript;
  if (!el || !window.Paddle) return;
  var token = el.getAttribute("data-paddle-token");
  if (!token) return;
  Paddle.Environment.set(el.getAttribute("data-paddle-environment") || "sandbox");
  Paddle.Initialize({ token: token });

  var txn = el.getAttribute("data-paddle-transaction");
  if (txn) {
    Paddle.Checkout.open({ transactionId: txn });
    return;
  }

  document.querySelectorAll("[data-paddle-price]").forEach(function (button) {
    button.addEventListener("click", function () {
      Paddle.Checkout.open({
        items: [{ priceId: button.getAttribute("data-paddle-price"), quantity: 1 }],
        customData: { account_id: button.getAttribute("data-account-id") },
        customer: { email: button.getAttribute("data-email") || undefined },
        settings: { successUrl: window.location.origin + "/billing" },
      });
    });
  });
})();
