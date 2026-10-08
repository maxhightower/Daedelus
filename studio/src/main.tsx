import React from "react";
import ReactDOM from "react-dom/client";
import { initApiBase } from "./api";
import App from "./App";
import { StudioProvider } from "./state";
import "./styles.css";

const root = ReactDOM.createRoot(document.getElementById("root")!);

initApiBase()
  .then(() =>
    root.render(
      <React.StrictMode>
        <StudioProvider>
          <App />
        </StudioProvider>
      </React.StrictMode>,
    ),
  )
  .catch((e) => root.render(<div className="fatal">Could not reach the Daedelus backend: {String(e)}</div>));
