import { NextRequest, NextResponse } from "next/server";
import { runSummarizeChain } from "@/lib/llm/chains";
import { z } from "zod";

const summarizeSchema = z.object({
  history: z
    .array(
      z.object({
        role: z.enum(["system", "user", "assistant"]),
        content: z.string(),
      })
    )
    .min(1, "History cannot be empty"),
  modelName: z.string().optional(),
});

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const validatedData = summarizeSchema.parse(body);

    const result = await runSummarizeChain(
      validatedData.history,
      validatedData.modelName
    );

    return NextResponse.json({
      success: true,
      data: {
        summary: result.summary,
        metrics: result.metrics,
      },
    });
  } catch (error: any) {
    console.error("[LLM Summarize API Error]:", error);

    if (error instanceof z.ZodError) {
      return NextResponse.json(
        { success: false, error: "Validation Error", details: error.issues },
        { status: 400 }
      );
    }

    return NextResponse.json(
      {
        success: false,
        error: error.message || "Failed to process summarization request.",
      },
      { status: 500 }
    );
  }
}
