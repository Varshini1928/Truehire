let registering = false;
$("auth-toggle").onclick = () => {
  registering = !registering;
  $("name-field").hidden = !registering;
  $("name").required = registering;
  $("auth-title").textContent = registering
    ? "Your next chapter starts here."
    : "Welcome back.";
  $("auth-subtitle").textContent = registering
    ? "Create your private intelligence workspace."
    : "Sign in to your intelligence workspace.";
  $("auth-submit").textContent = registering ? "Create account" : "Sign in";
  $("auth-toggle").textContent = registering ? "Sign in" : "Create an account";
  $("toggle-copy").textContent = registering
    ? "Already have an account?"
    : "New to TrueHire?";
  $("password").autocomplete = registering
    ? "new-password"
    : "current-password";
  $("auth-error").textContent = "";
};
$("auth-form").onsubmit = async (event) => {
  event.preventDefault();
  $("auth-submit").disabled = true;
  $("auth-error").textContent = "";
  try {
    await api("/api/auth/" + (registering ? "register" : "login"), {
      method: "POST",
      body: JSON.stringify({
        name: $("name").value,
        email: $("email").value,
        password: $("password").value,
      }),
    });
    location.href = "/dashboard";
  } catch (e) {
    $("auth-error").textContent = e.message;
  } finally {
    $("auth-submit").disabled = false;
  }
};
