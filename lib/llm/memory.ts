import {
  BaseMessage,
  HumanMessage,
  AIMessage,
  SystemMessage,
} from "@langchain/core/messages";

export interface InputChatMessage {
  role: "system" | "user" | "assistant";
  content: string;
}

/**
 * Converts simple input JSON chat messages to LangChain BaseMessage objects.
 */
export function formatToLangChainMessages(
  messages: InputChatMessage[],
  systemPrompt?: string
): BaseMessage[] {
  const result: BaseMessage[] = [];

  if (systemPrompt) {
    result.push(new SystemMessage(systemPrompt));
  }

  for (const msg of messages) {
    if (msg.role === "system") {
      result.push(new SystemMessage(msg.content));
    } else if (msg.role === "user") {
      result.push(new HumanMessage(msg.content));
    } else if (msg.role === "assistant") {
      result.push(new AIMessage(msg.content));
    }
  }

  return result;
}

/**
 * Formats past turns into a readable text block for memory summarization.
 */
export function formatConversationForSummary(messages: InputChatMessage[]): string {
  return messages
    .map((msg) => `${msg.role.toUpperCase()}: ${msg.content}`)
    .join("\n");
}
