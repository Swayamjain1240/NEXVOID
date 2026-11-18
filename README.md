# NEXVOID
### Multi-Tenant RAG & Retrieval Intelligence Engine

**Evidence-driven answers. Measurable retrieval. Safety by design.**

NEXVOID is a lightweight, multi-tenant Retrieval-Augmented Generation (RAG) service built in Python. It combines BM25 sparse retrieval, dense vector embeddings, and Reciprocal Rank Fusion (RRF) to retrieve relevant evidence and generate extractive, citation-backed answers.

Designed around minimal dependencies, tenant isolation, measurable performance, and reproducible evaluation, NEXVOID explores how reliable RAG systems can be engineered without unnecessary infrastructure complexity.

---

## Overview

Traditional RAG implementations often focus on generating answers while overlooking retrieval quality, tenant isolation, refusal reliability, and operational performance.

NEXVOID approaches these challenges as an engineering problem.

The system focuses on:

- Hybrid information retrieval.
- Evidence-backed answer generation.
- Multi-tenant data isolation.
- Prompt-injection refusal controls.
- Low-latency request processing.
- Reproducible evaluation and benchmarking.
- Modular architecture with enforced design constraints.

## Architecture

```text
                 User Request
                      |
                      v
              Request Validation
                      |
                      v
               Safety / Refusal Gate
                      |
                      v
              Tenant Identification
                      |
                      v
             Hybrid Retrieval Engine
                /           \
               v             v
          BM25 Search    Dense Search
               \             /
                \           /
                 v         v
              Reciprocal Rank
                 Fusion (RRF)
                      |
                      v
              Evidence Selection
                      |
                      v
             Extractive Answering
                      |
                      v
              Citation Generation
                      |
                      v
                Final Response
```

**Infrastructure**

- Python standard library HTTP server
- SQLite for persistent storage
- NumPy for numerical operations
- Lightweight, single-node deployment architecture

## Core Engineering Features

### 1. Hybrid Retrieval Engine

Combines complementary retrieval techniques to improve evidence discovery.

- **BM25:** Lexical retrieval for keyword-based matching.
- **Dense Retrieval:** Semantic similarity using vector embeddings.
- **Reciprocal Rank Fusion:** Combines rankings from sparse and dense retrieval.
- **Reranking:** Evaluated through controlled ablation experiments to identify retrieval bottlenecks.

### 2. Citation-Backed Answer Generation

- Retrieves evidence from the indexed document corpus.
- Produces extractive answers grounded in retrieved content.
- Associates answers with supporting citations.
- Avoids presenting unsupported information as retrieved evidence.

### 3. Multi-Tenant Isolation

- Separates retrieval contexts across tenants.
- Applies tenant-aware request handling.
- Evaluates cross-tenant isolation using dedicated security probes.

### 4. Safety-First Refusal Controls

- Implements a dedicated refusal gate.
- Evaluates prompt-injection handling against a frozen test set.
- Records trace identifiers for request-level investigation.
- Captures four per-stage timing measurements.
- Audits application logs for exposure of raw personal values.

### 5. Lightweight Architecture

- Uses Python standard-library HTTP infrastructure.
- Uses SQLite for persistence.
- Uses NumPy as the only runtime dependency.
- Enforces a 200-line ceiling for individual modules.
- Enforces 12 architecture rules through an import-graph test.

---

## Benchmark Results

The following results come from the project's recorded evaluation reports and apply to their stated datasets and operating conditions.

### Retrieval Quality

| Metric | Result |
|---|---:|
| Evaluation corpus | 18 documents |
| Frozen golden queries | 24 |
| Hybrid Recall@10 | 0.9583 |
| Confidence intervals | 95% Wilson intervals |
| Ablation configurations | 5 configurations × 2 encoders |

The ablation study evaluated retrieval configurations to isolate the reranking stage as a performance bottleneck.

### Latency and Throughput

| Benchmark condition | Result |
|---|---:|
| Burst workload | 10 concurrent users |
| Burst p95 latency | 37.2 ms |
| Sustained workload | 15 minutes |
| Sustained p95 latency | 19.56 ms |
| Failed requests | 0 |

These measurements describe the tested single-node workloads, not a guarantee of performance under arbitrary production traffic.

### Security Evaluation

| Security test | Result |
|---|---:|
| Prompt-injection refusal probes | 40/40 |
| Cross-tenant leakage probes | 0 leaks across 12 tenants |
| Audited log lines | 133 |
| Raw personal values observed in audited logs | 0 |

These are observed results from the tested security cases, not proof of universal prompt-injection resistance or complete privacy compliance.

### Testing and Reproducibility

| Engineering check | Result |
|---|---:|
| Automated tests | 149 |
| Architecture rules enforced | 12 |
| Architecture decision records | 12 |
| Frozen evaluation question sets | 4 |
| Determinism probe | 0 differences |

The project also includes a CI quality-gate failure demonstration and a clean-clone handover procedure.

---

## Evaluation Methodology

NEXVOID uses a structured evaluation process rather than relying on a single benchmark score.

### Retrieval Evaluation

- Uses frozen question sets.
- Measures Recall@10.
- Compares multiple retrieval configurations and encoders.
- Uses confidence intervals to communicate uncertainty.
- Uses ablation experiments to identify individual component contributions.

### Refusal Evaluation

- Uses dedicated prompt-injection test cases.
- Evaluates generalization across separate frozen evaluation sets.
- Tracks refusal behavior instead of assuming that a successful baseline guarantees robust protection.

### Performance Evaluation

- Measures p95 latency under burst and sustained workloads.
- Separates workload conditions when reporting latency.
- Records per-stage timings for request-level analysis.

### Reproducibility

- Maintains append-only evaluation records.
- Separates model fitting from evaluation decisions.
- Uses frozen evaluation sets to reduce accidental tuning against test data.
- Documents architectural decisions and rejected alternatives.

**Evaluation transparency:** One out-of-sample refusal evaluation exposed a substantial generalization failure, with the refusal gate score dropping from 1.0000 to 0.1667. A second signal partially recovered performance. This limitation is retained in the evaluation history rather than silently tuned away.

---

## Technology Stack

| Category | Technology |
|---|---|
| Language | Python |
| Numerical Computing | NumPy |
| Storage | SQLite |
| HTTP Infrastructure | Python Standard Library |
| Retrieval | BM25, Dense Embeddings |
| Ranking | Reciprocal Rank Fusion (RRF) |
| Evaluation | Frozen Golden Sets, Ablation Testing |
| Quality Assurance | Automated Tests, Import-Graph Checks |
| Architecture | Modular, Single-Node Design |

## Project Structure

The implementation is organized into small, independently testable modules.

```text
NEXVOID/
├── [application modules]
├── [retrieval components]
├── [evaluation reports]
├── [test suite]
├── [architecture decisions]
└── README.md
```

Refer to the repository for the current implementation and exact directory structure.

## Running the Project

Clone the repository:

```bash
git clone https://github.com/Swayamjain1240/NEXVOID.git
cd NEXVOID
```

Create and activate a virtual environment:

```bash
python -m venv .venv
```

Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

Install the runtime dependency:

```bash
pip install numpy
```

Follow the project's current configuration and startup instructions for launching the service and running its evaluation suite.

## Engineering Principles

NEXVOID follows several engineering principles:

1. **Measure before optimizing:** Establish baselines before changing retrieval or ranking configurations.
2. **Evidence over assumptions:** Every reported benchmark must have a corresponding evaluation record.
3. **Isolation by design:** Tenant boundaries must be tested, not merely assumed.
4. **Reproducibility:** Evaluation results should be independently repeatable.
5. **Minimal dependencies:** Avoid infrastructure complexity when a simpler implementation is sufficient.
6. **Transparent limitations:** Preserve failed experiments and report generalization weaknesses.

## Current Limitations

- Retrieval benchmarks use a relatively small 18-document corpus and 24-query golden set.
- Security results are limited to the documented probe cases.
- Single-node benchmarks should not be interpreted as distributed production capacity.
- Refusal-gate generalization has demonstrated an out-of-sample weakness.
- Retrieval quality does not independently establish answer correctness for every possible user query.

## Author

**Swayam Jain**

B.Tech Computer Science Engineering | AI Engineering | Backend Systems | RAG

- GitHub: [Swayamjain1240](https://github.com/Swayamjain1240)

---

**NEXVOID — Engineering retrieval systems where every answer has evidence and every metric has a methodology.**
