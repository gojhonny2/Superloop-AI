<div align="center">
  <br />
  <img src="logo.png" alt="Superloop" width="140" />
  <h1>Superloop</h1>
  <p><em>A manager-led autonomous coding loop where multiple AI models collaborate to build, test, and refine code until it works.</em></p>

  <p>
    <img src="https://img.shields.io/badge/version-0.1.0-blue" alt="Version" />
    <img src="https://img.shields.io/badge/status-alpha-orange" alt="Alpha" />
    <img src="https://img.shields.io/badge/python-3.10%2B-green" alt="Python" />
    <img src="https://img.shields.io/badge/license-MIT-lightgrey" alt="License" />
  </p>
</div>

---

## What is Superloop?

Superloop is an **autonomous development system** that uses a team of AI models to write code for you.

You give it a goal. It figures out the rest.

A **Manager AI** leads the process — it writes the first version of the code and creates a scoring judge to measure progress. Then **Specialist AIs** each try their own approach to improve it. The Manager reviews what everyone did, keeps the best parts, and repeats the cycle until the target score is hit.

Think of it as a coding team that works in a loop: **build → test → improve → repeat**.

---

## How It Works

```
You define a goal
       ↓
Manager writes the judge + first code attempt
       ↓
Judge scores it
       ↓
  ┌────┴────┐
  ↓         ↓
Spec A    Spec B     ← Each specialist tries a different fix
  ↓         ↓
Judge scores both
  ↓         ↓
  └────┬────┘
       ↓
Manager merges the best parts → Judge scores again
       ↓
Target hit? → One final verification round → Done ✓
Not yet?    → Loop back ↑
```

### The Key Ideas

- **Manager owns the solution.** It doesn't just pick the best specialist — it reads both attempts and integrates the useful parts into one canonical version.
- **Judge is auto-generated.** Superloop writes a real scoring script from your goal and data, so progress is measured by actual execution — not vibes.
- **Specialists explore, they don't manage.** They get the current code, score, and feedback, then try to push the score higher in their own way.
- **Every agent remembers.** Round history is injected into every prompt so no model forgets what was already tried.

---

## Quick Start

### 1. Clone & Install

```bash
git clone https://github.com/gojhonny2/Superloop-AI.git
cd Superloop-AI
pip install -e .
```

### 2. Set Up API Keys

Copy the example config and add your keys:

```bash
cp .env.example .env
```

You need **at least one** provider key. Superloop supports:

| Provider | Env Variable | Example Descriptor |
|---|---|---|
| OpenAI | `OPENAI_API_KEY` | `openai:gpt-4o` |
| Anthropic | `ANTHROPIC_API_KEY` | `anthropic:claude-3-5-sonnet-20240620` |
| Google Gemini | `GEMINI_API_KEY` | `gemini:gemini-1.5-pro` |
| OpenRouter | `OPENROUTER_API_KEY` | `openrouter:<model>` |
| Ollama (local) | `OLLAMA_KEY` optional, `OLLAMA_BASE_URL` optional | `ollama:<model>` |
| Groq | `GROQ_API_KEY` | `groq:<model>` |
| DeepSeek | `DEEPSEEK_API_KEY` | `deepseek:<model>` |
| Local / Custom | any | `custom:<model>:<base_url>:<env_key>` |

### 3. Launch the Dashboard

```bash
superloop --dashboard
```

Open **http://127.0.0.1:8000** → pick a workspace folder → describe your goal → hit Launch.

### 4. Or Run Headless

```bash
superloop \
  --goal "Build a script that cleans and validates the dataset" \
  --workspace ./my-project \
  --data ./my-project/data.csv \
  --target_score 0.8 \
  --max_iters 5
```

---

## Configuring the Swarm

By default, Superloop uses a **3-model swarm** (1 Manager + 2 Specialists). You can configure which models fill each role.

In your `.env`:

```bash
# Manager — leads the loop, writes the judge, integrates results
SUPERLOOP_MANAGER="gemini:gemini-1.5-pro"

# Specialists — two models that independently try to improve the code
SUPERLOOP_SPECIALISTS="openai:gpt-4o,anthropic:claude-3-5-sonnet-20240620"
```

Or pass them as CLI flags:

```bash
superloop --dashboard \
  --manager "openai:gpt-4o" \
  --specialists "anthropic:claude-3-5-sonnet-20240620,gemini:gemini-1.5-pro"
```

---

## Project Structure

```
superloop/
├── api/
│   ├── server.py              # FastAPI backend + SSE event streaming
│   └── static/                # Dashboard frontend (HTML/CSS/JS)
├── core/
│   ├── lead_engine.py         # The main loop — manager-led orchestration
│   ├── judge_bootstrapper.py  # Auto-generates judge_logic.py from your goal
│   ├── run_context.py         # Run state and metadata management
│   ├── run_memory.py          # Cross-round memory for agent context
│   └── runtime_workspace.py   # Workspace file operations
├── judges/
│   ├── runtime_judge.py       # Executes judge_logic.py and parses scores
│   └── base_judge.py          # Judge interface
├── models/
│   ├── runtime_factory.py     # Parses "provider:model" descriptors
│   ├── openai_model.py        # OpenAI / OpenRouter / compatible APIs
│   ├── anthropic_model.py     # Anthropic Claude models
│   ├── reliable_gemini_model.py  # Google Gemini with retry logic
│   ├── universal_model.py     # Generic OpenAI-compatible endpoint
│   └── base_model.py          # Model interface
├── shells/
│   └── app_cli.py             # CLI entrypoint (dashboard + headless)
├── tests/                     # Unit tests
├── .env.example               # Configuration template
├── pyproject.toml              # Package metadata
└── superloop_version.py       # Version: 0.1.0 (alpha)
```

---

## Run Artifacts

Every run saves to `runs/<run_id>/` inside your workspace:

| File | What It Is |
|---|---|
| `judge_logic.py` | The auto-generated scoring script |
| `candidate_solution.py` | Latest candidate code |
| `candidate_solution_verified.py` | Final verified solution (when target is hit) |
| `events.jsonl` | Full event log for the dashboard timeline |
| `swarm_research_log.jsonl` | Detailed round-by-round research trail |
| `summary.json` | Run metadata and final results |
| `artifacts/` | Snapshot of every candidate at each round |

---

## CLI Reference

```
superloop [OPTIONS]

Options:
  --dashboard           Launch the web dashboard
  --host HOST           Dashboard host (default: 0.0.0.0)
  --port PORT           Dashboard port (default: 8000)
  --goal TEXT            What the manager should build
  --workspace DIR        Working directory for the run
  --data PATH            Path to evaluation data file
  --judge_brief TEXT     Instructions for the auto-generated judge
  --operator_brief TEXT  Extra guidance for the manager
  --target_score FLOAT   Goal score to reach (default: 0.8)
  --max_iters INT        Maximum optimization rounds (default: 5)
  --resume               Resume from the latest run
  --manager DESC         Manager model descriptor
  --specialists DESC     Comma-separated specialist descriptors
  --version              Show version and exit
```

---

## Roadmap

- [ ] Stronger judge templates for common task types
- [ ] Diff viewer and score comparison graphs in the dashboard
- [ ] Provider retry hardening and timeout controls
- [ ] Plugin system for custom model providers
- [ ] Multi-file project support

---

## Contributing

Superloop is in early alpha. If you find bugs or have ideas, [open an issue](https://github.com/gojhonny2/Superloop-AI/issues).

---

<div align="center">
  <sub>Built with the idea that AI models work better as a team.</sub>
</div>
