import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import App from "./App";
import Landing from "./pages/Landing";
import "./index.css";
import { PrefsProvider } from "./lib/prefs";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <PrefsProvider>
        <Routes>
          <Route path="/welcome" element={<Landing />} />
          <Route path="/*" element={<App />} />
        </Routes>
      </PrefsProvider>
    </BrowserRouter>
  </React.StrictMode>,
);
