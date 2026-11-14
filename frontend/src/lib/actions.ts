import { useNavigate } from "react-router-dom";
import { Action, api, RestockDraft } from "./api";

/** Runs the action buttons attached to chat answers and insights. Every action creates a
 * DRAFT or opens a page - nothing is ever sent without the owner. */
export function useActionRunner(language: string = "hinglish") {
  const navigate = useNavigate();
  return async (a: Action) => {
    const p = a.payload ?? {};
    switch (a.type) {
      case "restock_draft": {
        const d = await api.post<RestockDraft>("/restock/draft", {
          budget_rs: p.budget_rs ?? null, supplier: p.supplier_id ?? null, language,
        });
        navigate("/orders", { state: { draftId: d.draft_id } });
        break;
      }
      case "campaign_draft": {
        const c = await api.post<{ campaign_id: string }>("/campaigns", {
          goal: p.goal, item_a: p.item_a ?? null, item_b: p.item_b ?? null, language,
          segment: p.segment ?? null, holdout_pct: p.holdout_pct ?? null,
        });
        navigate(`/campaigns/${c.campaign_id}`);
        break;
      }
      case "open_campaign":
        navigate(`/campaigns/${p.campaign_id}`);
        break;
      case "ask":
        navigate("/", { state: { ask: p.question, fresh: Date.now() } });
        break;
      case "open_whatsapp":
        window.open(p.url, "_blank", "noopener,noreferrer");
        break;
      case "download_csv":
        window.open(p.path, "_blank");
        break;
      default:
        break;
    }
  };
}

export const RUNNABLE = new Set(["restock_draft", "campaign_draft", "open_campaign", "ask", "open_whatsapp",
  "download_csv"]);
