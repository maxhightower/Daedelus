import React, { useState } from "react";
import ReactDOM from "react-dom/client";
import { initApiBase, initAuth, login } from "./api";
import App from "./App";
import { StudioProvider } from "./state";
import "./styles.css";

const root = ReactDOM.createRoot(document.getElementById("root")!);

// A file dropped anywhere outside the canvas must not navigate the window to that file
// (the desktop shell hands OS drag-and-drop to the webview: dragDropEnabled is false).
for (const type of ["dragover", "drop"]) {
  window.addEventListener(type, (e) => {
    if ((e as DragEvent).dataTransfer?.types.includes("Files")) e.preventDefault();
  });
}

function Studio() {
  return (
    <React.StrictMode>
      <StudioProvider>
        <App />
      </StudioProvider>
    </React.StrictMode>
  );
}

/** Shown when the server requires an API token and no session exists yet. The token is
 * exchanged for an HttpOnly session cookie (or kept in memory for a remote server) and is
 * never written to storage or the address bar. */
function Login() {
  const [token, setToken] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  return (
    <div className="login-screen" data-testid="login">
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          setErr(null);
          try {
            await login(token.trim());
            setToken("");
            root.render(<Studio />);
          } catch (ex: any) {
            setErr(String(ex?.message ?? ex));
          } finally {
            setBusy(false);
          }
        }}
      >
        <h2>Daedelus</h2>
        <p>This server requires an access token.</p>
        <input
          type="password"
          autoComplete="off"
          placeholder="API token"
          value={token}
          onChange={(e) => setToken(e.target.value)}
          data-testid="login-token"
        />
        <button type="submit" disabled={busy || !token.trim()} data-testid="login-submit">
          Sign in
        </button>
        {err && <div className="error">{err}</div>}
      </form>
    </div>
  );
}

initApiBase()
  .then(() => initAuth())
  .then((mode) => root.render(mode === "required" ? <Login /> : <Studio />))
  .catch((e) => root.render(<div className="fatal">Could not reach the Daedelus backend: {String(e)}</div>));
