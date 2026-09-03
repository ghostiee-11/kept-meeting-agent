# System

```mermaid
flowchart TB
    subgraph client["Browser"]
        UI["Next.js console<br/>run · execution · questions · people · ops"]
    end

    subgraph vercel["Vercel"]
        FE["Next.js 16 App Router"]
    end

    subgraph render["Render, free tier"]
        API["FastAPI<br/>auth · rate limit · quotas"]
        GRAPH["LangGraph runtime<br/>Chief of Staff + 10 agents"]
        SVC["Deterministic services<br/>verifier · risk · roster · calendar"]
        MOCK["Mock task API<br/>idempotency · fault injection"]
    end

    subgraph data["Neon Postgres + pgvector"]
        DB[("meetings · commitments<br/>commitment_events · runs<br/>agent_trace · clarifications")]
    end

    subgraph providers["Model providers"]
        OAI["OpenAI<br/>reasoning + routing"]
        GROQ["Groq<br/>fallback, free"]
        GEM["Gemini<br/>fallback, free"]
    end

    TAV["Tavily<br/>web search"]
    CRON["GitHub Actions<br/>nightly sweep"]

    UI --> FE
    FE -->|"REST + SSE"| API
    API --> GRAPH
    GRAPH --> SVC
    GRAPH --> MOCK
    GRAPH --> OAI
    GRAPH -.->|"on 429 or 5xx"| GROQ
    GRAPH -.->|"then"| GEM
    GRAPH --> TAV
    API --> DB
    GRAPH --> DB
    CRON -->|"POST /internal/sweep<br/>also keeps the instance warm"| API
```

The dotted edges are the fallback chain. Every tier is an ordered list of
provider and model pairs, filtered at boot to whatever has credentials, so the
system runs on free keys alone when that is all there is.
