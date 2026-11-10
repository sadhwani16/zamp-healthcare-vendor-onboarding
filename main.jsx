import React from "react";
import { createRoot } from "react-dom/client";
import Frontend from "./Frontend.jsx";
import "./style.css";

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <Frontend />
  </React.StrictMode>
);
