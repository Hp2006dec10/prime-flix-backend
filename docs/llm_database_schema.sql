-- =========================================================================
-- LLM PERFORMANCE & CONVERSATION MEMORY DATABASE SCHEMA (PostgreSQL + pgvector)
-- =========================================================================

-- Enable pgvector extension for Vector Embedding storage (RAG & semantic search)
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS vector;

-- Enums
CREATE TYPE message_role AS ENUM ('SYSTEM', 'USER', 'ASSISTANT', 'TOOL');
CREATE TYPE session_status AS ENUM ('ACTIVE', 'ARCHIVED', 'EXPIRED');

-- -------------------------------------------------------------------------
-- 1. CONVERSATION MEMORY TABLES
-- -------------------------------------------------------------------------

-- Session memory table
CREATE TABLE conversation_sessions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id VARCHAR(255) NOT NULL,
    title VARCHAR(255),
    system_prompt TEXT,
    summary_context TEXT,
    token_count INT DEFAULT 0,
    status session_status DEFAULT 'ACTIVE',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_sessions_user_id ON conversation_sessions(user_id);

-- Chat messages history
CREATE TABLE chat_messages (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id UUID NOT NULL REFERENCES conversation_sessions(id) ON DELETE CASCADE,
    role message_role NOT NULL,
    content TEXT NOT NULL,
    name VARCHAR(255),
    prompt_tokens INT,
    completion_tokens INT,
    metadata JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_chat_messages_session_id ON chat_messages(session_id);

-- Vector memory store (OpenAI text-embedding-3-small vectors: 1536 dimensions)
CREATE TABLE vector_memory_chunks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id UUID NOT NULL REFERENCES conversation_sessions(id) ON DELETE CASCADE,
    message_id UUID REFERENCES chat_messages(id) ON DELETE SET NULL,
    chunk_content TEXT NOT NULL,
    embedding vector(1536),
    metadata JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_vector_chunks_session_id ON vector_memory_chunks(session_id);
CREATE INDEX idx_vector_chunks_embedding ON vector_memory_chunks USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- -------------------------------------------------------------------------
-- 2. LLM PERFORMANCE & METRICS MONITORING TABLES
-- -------------------------------------------------------------------------

-- Detailed execution telemetry log for every API completion call
CREATE TABLE llm_execution_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id UUID REFERENCES conversation_sessions(id) ON DELETE SET NULL,
    message_id UUID REFERENCES chat_messages(id) ON DELETE SET NULL,
    provider VARCHAR(50) DEFAULT 'openai',
    model VARCHAR(100) NOT NULL,
    prompt_tokens INT DEFAULT 0,
    completion_tokens INT DEFAULT 0,
    total_tokens INT DEFAULT 0,
    latency_ms INT NOT NULL,
    ttft_ms INT,
    cost_usd NUMERIC(10, 6) DEFAULT 0.000000,
    status_code INT DEFAULT 200,
    error_message TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_llm_exec_session_id ON llm_execution_logs(session_id);
CREATE INDEX idx_llm_exec_model ON llm_execution_logs(model);
CREATE INDEX idx_llm_exec_created_at ON llm_execution_logs(created_at);

-- User feedback & LLM Evaluation metrics
CREATE TABLE llm_evaluation_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    execution_log_id UUID UNIQUE NOT NULL REFERENCES llm_execution_logs(id) ON DELETE CASCADE,
    rating INT,
    user_feedback TEXT,
    hallucination_score FLOAT,
    sentiment_score FLOAT,
    toxicity_score FLOAT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- User token quotas and rate limiting
CREATE TABLE llm_user_quota_rate_limits (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id VARCHAR(255) UNIQUE NOT NULL,
    daily_token_limit INT DEFAULT 100000,
    daily_tokens_used INT DEFAULT 0,
    monthly_cost_limit_usd NUMERIC(10, 2) DEFAULT 10.00,
    monthly_cost_used_usd NUMERIC(10, 6) DEFAULT 0.000000,
    last_reset_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
