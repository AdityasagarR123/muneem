import { useNavigate } from "react-router-dom";
import { Action, api, RestockDraft } from "./api";

/** Executes the action buttons that come back in chat answers and alerts. */
export function useActionRunner(language: string = "hinglish") {
  const navigate = useNavigate();
  return async (a: Action) => {
    switch (a.type) {
      case "restock_draft": {
        const d = await api.post<RestockDraft>("/restock/draft", {
          budget_rs: a.payload.budget_rs ?? null,
          supplier: a.payload.supplier_id ?? null,
          language,
        });
        navigate("/restock", { state: { draftId: d.draft_id } });
        break;
      }
      case "open_whatsapp":
        window.open(a.payload.url, "_blank", "noopener,noreferrer");
        break;
      case "download_csv":
        window.open(a.payload.path, "_blank");
        break;
      default:
        break;
    }
  };
}

export const RUNNABLE = new Set(["restock_draft", "open_whatsapp", "download_csv"]);
