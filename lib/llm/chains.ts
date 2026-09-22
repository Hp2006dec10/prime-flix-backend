import { getChatOpenAIModel } from "./client";
import { formatToLangChainMessages, formatConversationForSummary, InputChatMessage } from "./memory";
import { LLMTracer, PerformanceMetrics } from "./tracer";
import { PromptTemplate } from "@langchain/core/prompts";
import { StringOutputParser } from "@langchain/core/output_parsers";

export interface ChatChainOptions {
  prompt: string;
  systemPrompt?: string;
  conversationHistory?: InputChatMessage[];
  modelName?: string;
  temperature?: number;
  maxTokens?: number;
}

export interface ChatChainResponse {
  content: string;
  metrics: PerformanceMetrics;
}

/**
 * Standard Chat Completion using LangChain OpenAI model.
 */
export async function runChatChain(options: ChatChainOptions): Promise<ChatChainResponse> {
  const modelName = options.modelName || "gpt-4o-mini";
  const tracer = new LLMTracer(modelName);

  const model = getChatOpenAIModel({
    modelName,
    temperature: options.temperature,
    maxTokens: options.maxTokens,
  });

  const history = options.conversationHistory || [];
  const messages = formatToLangChainMessages(
    [...history, { role: "user", content: options.prompt }],
    options.systemPrompt
  );

  const response = await model.invoke(messages);
  const content = typeof response.content === "string" 
    ? response.content 
    : JSON.stringify(response.content);

  const usage = response.usage_metadata ? {
    promptTokens: response.usage_metadata.input_tokens,
    completionTokens: response.usage_metadata.output_tokens,
    totalTokens: response.usage_metadata.total_tokens,
  } : undefined;

  const metrics = tracer.finalize(usage);

  return {
    content,
    metrics,
  };
}

/**
 * Real-time Streaming Chain using LangChain OpenAI model.
 */
export async function runStreamChain(
  options: ChatChainOptions,
  onChunk?: (chunk: string) => void
) {
  const modelName = options.modelName || "gpt-4o-mini";
  const tracer = new LLMTracer(modelName);

  const model = getChatOpenAIModel({
    modelName,
    temperature: options.temperature,
    maxTokens: options.maxTokens,
    streaming: true,
  });

  const history = options.conversationHistory || [];
  const messages = formatToLangChainMessages(
    [...history, { role: "user", content: options.prompt }],
    options.systemPrompt
  );

  const stream = await model.stream(messages);

  return {
    stream,
    tracer,
  };
}

/**
 * Conversation Memory Summarizer Chain.
 */
export async function runSummarizeChain(
  history: InputChatMessage[],
  modelName: string = "gpt-4o-mini"
): Promise<{ summary: string; metrics: PerformanceMetrics }> {
  const tracer = new LLMTracer(modelName);
  const formattedHistory = formatConversationForSummary(history);

  const promptTemplate = PromptTemplate.fromTemplate(
    `You are a conversation summarization assistant. Summarize the following chat transcript into a concise memory context. Retain key facts, preferences, decisions, and context points.
    
Transcript:
{transcript}

Concise Summary:`
  );

  const model = getChatOpenAIModel({ modelName, temperature: 0.2 });
  const chain = promptTemplate.pipe(model).pipe(new StringOutputParser());

  const summary = await chain.invoke({ transcript: formattedHistory });
  const metrics = tracer.finalize();

  return {
    summary,
    metrics,
  };
}
