import { NextRequest, NextResponse } from "next/server";
import { runChatChain } from "@/lib/llm/chains";
import { z } from "zod";

const chatSchema = z.object({
  prompt: z.string().min(1, "Prompt cannot be empty"),
  systemPrompt: z.string().optional(),
  conversationHistory: z
    .array(
      z.object({
        role: z.enum(["system", "user", "assistant"]),
        content: z.string(),
      })
    )
    .optional(),
  modelName: z.string().optional(),
  temperature: z.number().min(0).max(2).optional(),
  maxTokens: z.number().positive().optional(),
});

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const validatedData = chatSchema.parse(body);

    const result = await runChatChain(validatedData);

    return NextResponse.json({
      success: true,
      data: {
        message: result.content,
        metrics: result.metrics,
      },
    });
  } catch (error: any) {
    console.error("[LLM Chat API Error]:", error);

    if (error instanceof z.ZodError) {
      return NextResponse.json(
        { success: false, error: "Validation Error", details: error.issues },
        { status: 400 }
      );
    }

    return NextResponse.json(
      {
        success: false,
        error: error.message || "Failed to process chat completion request.",
      },
      { status: 500 }
    );
  }
}
