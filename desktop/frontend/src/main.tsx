import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@google/model-viewer";
import { AppShell } from "./app/AppShell";
import { ScreenCapturePicker } from "./features/capture/ScreenCapturePicker";
import "./shared/tokens/tokens.css";
import "./shared/tokens/amber-workshop.css";

const captureToken = new URLSearchParams(window.location.search).get(
  "screen-capture",
);
const captureParams = new URLSearchParams(window.location.search);
const sourceWidth = Number(captureParams.get("source-width"));
const sourceHeight = Number(captureParams.get("source-height"));

createRoot(document.getElementById("root")!).render(
  captureToken && captureToken !== "__standby__" ? (
    <ScreenCapturePicker
      token={captureToken}
      sourceSize={
        sourceWidth > 0 && sourceHeight > 0
          ? { width: sourceWidth, height: sourceHeight }
          : undefined
      }
    />
  ) : captureToken ? null : (
    <StrictMode>
      <AppShell />
    </StrictMode>
  ),
);
