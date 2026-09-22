import { ChatOpenAI } from "@langchain/openai";

export interface LLMClientOptions {
  modelName?: string;
  temperature?: number;
  maxTokens?: number;
  streaming?: boolean;
  apiKey?: string;
}

/**
 * Creates and configures a LangChain ChatOpenAI client instance.
 */
export function getChatOpenAIModel(options: LLMClientOptions = {}): ChatOpenAI {
  const apiKey = options.apiKey || process.env.OPENAI_API_KEY;
  
  if (!apiKey) {
    throw new Error(
      "OpenAI API Key missing. Set OPENAI_API_KEY in environment variables or pass apiKey in options."
    );
  }

  return new ChatOpenAI({
    openAIApiKey: apiKey,
    modelName: options.modelName || process.env.OPENAI_MODEL || "gpt-4o-mini",
    temperature: options.temperature ?? 0.7,
    maxTokens: options.maxTokens,
    streaming: options.streaming ?? false,
  });
}
