export interface PerformanceMetrics {
  startTime: number;
  endTime?: number;
  latencyMs?: number;
  ttftMs?: number; // Time-To-First-Token
  promptTokens?: number;
  completionTokens?: number;
  totalTokens?: number;
  estimatedCostUsd?: number;
  model: string;
}

// Pricing rates per 1k tokens (USD)
const MODEL_PRICING: Record<string, { prompt: number; completion: number }> = {
  "gpt-4o": { prompt: 0.0025, completion: 0.0100 },
  "gpt-4o-mini": { prompt: 0.00015, completion: 0.0006 },
  "gpt-4-turbo": { prompt: 0.01, completion: 0.03 },
  "gpt-3.5-turbo": { prompt: 0.0005, completion: 0.0015 },
};

/**
 * Calculates estimated cost for model generation based on token counts.
 */
export function calculateLLMCost(
  model: string,
  promptTokens: number = 0,
  completionTokens: number = 0
): number {
  const rates = MODEL_PRICING[model] || MODEL_PRICING["gpt-4o-mini"];
  const promptCost = (promptTokens / 1000) * rates.prompt;
  const completionCost = (completionTokens / 1000) * rates.completion;
  return Number((promptCost + completionCost).toFixed(6));
}

/**
 * Performance metrics tracker helper class.
 */
export class LLMTracer {
  private startTime: number;
  private firstTokenTime?: number;
  private model: string;

  constructor(model: string = "gpt-4o-mini") {
    this.startTime = Date.now();
    this.model = model;
  }

  public recordFirstToken() {
    if (!this.firstTokenTime) {
      this.firstTokenTime = Date.now();
    }
  }

  public finalize(usage?: { promptTokens?: number; completionTokens?: number; totalTokens?: number }): PerformanceMetrics {
    const endTime = Date.now();
    const latencyMs = endTime - this.startTime;
    const ttftMs = this.firstTokenTime ? this.firstTokenTime - this.startTime : undefined;

    const promptTokens = usage?.promptTokens || 0;
    const completionTokens = usage?.completionTokens || 0;
    const totalTokens = usage?.totalTokens || (promptTokens + completionTokens);

    const estimatedCostUsd = calculateLLMCost(this.model, promptTokens, completionTokens);

    return {
      startTime: this.startTime,
      endTime,
      latencyMs,
      ttftMs,
      promptTokens,
      completionTokens,
      totalTokens,
      estimatedCostUsd,
      model: this.model,
    };
  }
}
