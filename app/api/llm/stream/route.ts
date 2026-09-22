import { NextRequest, NextResponse } from "next/server";
import { runStreamChain } from "@/lib/llm/chains";
import { z } from "zod";

const streamSchema = z.object({
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
});

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const validatedData = streamSchema.parse(body);

    const { stream, tracer } = await runStreamChain(validatedData);
    const encoder = new TextEncoder();

    const customReadableStream = new ReadableStream({
      async start(controller) {
        let isFirst = true;

        for await (const chunk of stream) {
          if (isFirst) {
            tracer.recordFirstToken();
            isFirst = false;
          }

          const textChunk = typeof chunk.content === "string" ? chunk.content : "";
          if (textChunk) {
            controller.enqueue(
              encoder.encode(`data: ${JSON.stringify({ text: textChunk })}\n\n`)
            );
          }
        }

        const metrics = tracer.finalize();
        controller.enqueue(
          encoder.encode(`data: ${JSON.stringify({ metrics, done: true })}\n\n`)
        );
        controller.close();
      },
    });

    return new NextResponse(customReadableStream, {
      headers: {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache, no-transform",
        Connection: "keep-alive",
      },
    });
  } catch (error: any) {
    console.error("[LLM Stream API Error]:", error);

    if (error instanceof z.ZodError) {
      return NextResponse.json(
        { success: false, error: "Validation Error", details: error.issues },
        { status: 400 }
      );
    }

    return NextResponse.json(
      {
        success: false,
        error: error.message || "Failed to process LLM stream request.",
      },
      { status: 500 }
    );
  }
}
