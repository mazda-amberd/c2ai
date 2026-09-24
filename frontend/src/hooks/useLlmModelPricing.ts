import { useEffect, useState } from "react";

import {
  checkLlmModelPricing,
  listPricedLlmModels,
  type ApiLlmModel,
} from "@api/services/registeredApplications";

export type LlmPricingState =
  | { status: "idle" }
  | { status: "checking" }
  | { status: "priced"; provider: string | null }
  | { status: "unpriced"; message: string; provider: string | null }
  | { status: "error"; message: string };

/** Keystrokes are coalesced for this long before a request goes out, so a
 *  fast typist doesn't fire one call per character while the result still
 *  updates as they type. */
const DEBOUNCE_MS = 200;

/** Live pricing check for the LLM Model Name field: calls
 *  GET /llm-models/pricing?model_name=… as the user types and reports
 *  whether Athena can price that model. A stale response (the name changed
 *  while the request was in flight) is aborted and ignored. */
export function useLlmModelPricing(modelName: string): LlmPricingState {
  const [state, setState] = useState<LlmPricingState>({ status: "idle" });
  const trimmed = modelName.trim();

  useEffect(() => {
    if (!trimmed) return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      setState({ status: "checking" });
      checkLlmModelPricing(trimmed, controller.signal)
        .then((res) => {
          if (controller.signal.aborted) return;
          setState(
            res.pricing_available
              ? { status: "priced", provider: res.provider }
              : {
                  status: "unpriced",
                  provider: res.provider,
                  message: res.message ?? `Pricing not available for the model '${trimmed}'.`,
                },
          );
        })
        .catch((err: unknown) => {
          if (controller.signal.aborted) return;
          setState({
            status: "error",
            message: err instanceof Error ? err.message : "Could not check model pricing.",
          });
        });
    }, DEBOUNCE_MS);

    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [trimmed]);

  // An empty name has nothing to check — report idle without touching state
  // (the last result is simply superseded once the user types again).
  return trimmed ? state : { status: "idle" };
}

/** Priced model names for the field's datalist suggestions. */
export function useLlmModelSuggestions(enabled: boolean): ApiLlmModel[] {
  const [models, setModels] = useState<ApiLlmModel[]>([]);
  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    listPricedLlmModels()
      .then((list) => {
        if (!cancelled) setModels(list);
      })
      .catch(() => {
        /* suggestions are optional */
      });
    return () => {
      cancelled = true;
    };
  }, [enabled]);
  return models;
}
