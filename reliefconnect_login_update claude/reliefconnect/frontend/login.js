(function () {
  const $ = (id) => document.getElementById(id);
  const errorBox = $("error");

  // Already signed in? Go straight to the dashboard.
  fetch("/api/auth/me").then((r) => { if (r.ok) window.location.replace("/"); }).catch(() => {});

  function showError(message) {
    errorBox.textContent = message;
    errorBox.hidden = false;
  }
  function clearError() { errorBox.hidden = true; }

  function selectTab(which) {
    const login = which === "login";
    $("tab-login").classList.toggle("active", login);
    $("tab-register").classList.toggle("active", !login);
    $("tab-login").setAttribute("aria-selected", login);
    $("tab-register").setAttribute("aria-selected", !login);
    $("login-form").hidden = !login;
    $("register-form").hidden = login;
    $("forgot-form").hidden = true;
    clearError();
  }
  $("tab-login").addEventListener("click", () => selectTab("login"));
  $("tab-register").addEventListener("click", () => selectTab("register"));

  function selectLoginMethod(which) {
    const password = which === "password";
    $("method-password").classList.toggle("active", password);
    $("method-phone").classList.toggle("active", !password);
    $("method-password").setAttribute("aria-selected", password);
    $("method-phone").setAttribute("aria-selected", !password);
    $("password-login").hidden = !password;
    $("phone-login").hidden = password;
    clearError();
  }
  $("method-password").addEventListener("click", () => selectLoginMethod("password"));
  $("method-phone").addEventListener("click", () => selectLoginMethod("phone"));

  $("forgot-link").addEventListener("click", () => {
    $("login-form").hidden = true;
    $("register-form").hidden = true;
    $("forgot-form").hidden = false;
    $("tab-login").classList.remove("active");
    $("tab-register").classList.remove("active");
    clearError();
  });
  $("back-login").addEventListener("click", () => selectTab("login"));

  document.querySelectorAll(".show").forEach((btn) => {
    btn.addEventListener("click", () => {
      const input = $(btn.dataset.target);
      const hidden = input.type === "password";
      input.type = hidden ? "text" : "password";
      btn.textContent = hidden ? "Hide" : "Show";
    });
  });

  async function post(url, data) {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    const json = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(json.error || "Something went wrong. Please try again.");
    return json;
  }

  async function submit(form, url, data) {
    const button = form.querySelector(".submit");
    const label = button.textContent;
    clearError();
    button.disabled = true;
    button.textContent = "Please wait...";
    try {
      await post(url, data);
      window.location.replace("/");
    } catch (err) {
      showError(err.message);
      button.disabled = false;
      button.textContent = label;
    }
  }

  $("send-otp").addEventListener("click", async () => {
    const phone = $("login-phone").value.trim();
    if (!phone) return showError("Enter your registered phone number.");
    try {
      const result = await post("/api/auth/phone/send-otp", { phone });
      if (result.demo_otp) $("login-otp").value = result.demo_otp;
      showError("Demo OTP generated. Enter the 6-digit code.");
    } catch (err) {
      showError(err.message);
    }
  });

  $("otp-login").addEventListener("click", async () => {
    const phone = $("login-phone").value.trim();
    const otp = $("login-otp").value.trim();
    if (!phone || !otp) return showError("Enter your phone number and OTP.");
    try {
      await post("/api/auth/phone/login", { phone, otp });
      window.location.replace("/");
    } catch (err) {
      showError(err.message);
    }
  });

  $("send-reset").addEventListener("click", async () => {
    const email = $("reset-email").value.trim();
    if (!email) return showError("Enter your account email.");
    try {
      const result = await post("/api/auth/forgot-password", { email });
      if (result.demo_reset_code) $("reset-code").value = result.demo_reset_code;
      showError("If the account exists, a reset code has been generated.");
    } catch (err) {
      showError(err.message);
    }
  });

  $("reset-submit").addEventListener("click", async () => {
    const email = $("reset-email").value.trim();
    const code = $("reset-code").value.trim();
    const password = $("reset-password").value;
    if (!email || !code || password.length < 8) {
      return showError("Enter the email, reset code, and an 8-character password.");
    }
    try {
      await post("/api/auth/reset-password", { email, code, password });
      showError("Password updated. You can now sign in.");
      setTimeout(() => selectTab("login"), 800);
    } catch (err) {
      showError(err.message);
    }
  });

  $("login-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const email = $("login-email").value.trim();
    const password = $("login-password").value;
    if (!email || !password) return showError("Enter your email and password.");
    submit(e.target, "/api/auth/login", { email, password });
  });

  $("register-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const password = $("reg-password").value;
    if (password.length < 8) return showError("Password must be at least 8 characters.");
    if (password !== $("reg-confirm").value) return showError("The two passwords do not match.");
    submit(e.target, "/api/auth/register", {
      full_name: $("reg-name").value.trim(),
      email: $("reg-email").value.trim(),
      phone: $("reg-phone").value.trim(),
      password,
      role: document.querySelector('input[name="role"]:checked').value,
    });
  });
})();
