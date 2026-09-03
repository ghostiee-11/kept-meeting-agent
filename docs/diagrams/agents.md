# Agents

```mermaid
flowchart TB
    CoS{{"Chief of Staff<br/><i>routes · replans · finishes</i>"}}

    subgraph INT["Intelligence"]
        direction TB
        SCR["Scribe<br/><i>turns, speakers</i>"]
        AN["Analyst ×3<br/><i>decisions · commitments · blockers</i>"]
        SK["Skeptic<br/><i>challenges each candidate</i>"]
        SCR --> AN --> VER[["Verifier<br/>code, not an agent"]] --> SK
    end

    subgraph RES["Resolution"]
        direction TB
        ATT["Attributor<br/><i>owner, or abstain</i>"]
        CHR["Chronos<br/><i>spoken date to real date</i>"]
        RSCH["Researcher<br/><i>cited context</i>"]
        ATT --> CHR --> RSCH
    end

    subgraph HIS["History"]
        HST["Historian<br/><i>slippage · silence · restatement</i>"]
    end

    subgraph EXE["Execution"]
        direction TB
        RISK[["Risk scorer<br/>pure function"]]
        OP["Operator<br/><i>creates tasks</i>"]
        HER["Herald<br/><i>drafts recap and nudges</i>"]
        RISK --> OP --> HER
    end

    CoS --> INT
    CoS --> RES
    CoS --> HIS
    CoS --> EXE
    INT -.->|"most of a batch rejected"| CoS
    RES -.->|"owner unresolved"| ASK(["Question for a human"])

    style VER stroke-dasharray: 4 4
    style RISK stroke-dasharray: 4 4
```

The dashed boxes are plain code rather than agents, because they must never be
creative. The dotted arrows are the two places the system declines to proceed
on its own: re-extraction when review threw out too much, and a question when an
owner cannot be settled.

**The tool belt is the security boundary.** The Analyst reads the raw
transcript and has no tools. The Operator has every write tool and never reads
the transcript. An instruction hidden in a meeting has nothing to reach.
