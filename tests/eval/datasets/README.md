# Evaluation Datasets

This directory contains evaluation datasets for testing agent behavior.

- `basic-dataset.json` — week 1 cases (compare, beginner rec, price, medical, mug brewing).
- `week2-dataset.json` — week 2 cases: shop (`gift_under_20_eur`, `partner_shop_showcase`),
  brewing (`brew_mug_only`), onboarding (`onboarding_new_user`),
  next-step chips (`next_steps_three_recs`).
- `tea-types-dataset.json` — TEA-23: mixed cart + white/yellow/red/puerh/GABA cases.
- `mixed-order-case.json` — TEA-23 acceptance slice (mixed cart only, for quota-friendly generate).

## Running Evaluations

### Default Dataset
```bash
# Generate traces using the default dataset
agents-cli eval generate
agents-cli eval grade
```

### Local gotchas (AI Studio key, no GCP project)

- `eval generate` needs any non-empty `GOOGLE_CLOUD_PROJECT` (the Vertex eval SDK
  builds a BigQuery client at startup; a placeholder works, no calls are made):
  ` $env:GOOGLE_CLOUD_PROJECT='teabot-local-eval'; agents-cli eval generate ... `
- Free tier for `gemini-3.6-flash` is ~5 req/min and ~20 req/day. Run large
  datasets as single-case slices (see `.tmp_eval/case_*.json`) with ~60 s pauses,
  then merge with `scripts/merge_traces.py`.
- The judge (`tests/eval/response_quality.py`) runs on `gemini-3.5-flash` with a
  `gemini-3.1-flash-lite` fallback so it doesn't share the agent's daily quota.

### Custom Dataset
```bash
# Generate traces for a custom dataset
agents-cli eval generate --dataset tests/eval/datasets/custom-dataset.json --output custom_traces/
agents-cli eval grade --metrics general_quality --traces custom_traces/
```

## Dataset Format

Each dataset file follows the Gemini Enterprise Agent Platform Evaluation
dataset format. An eval case may use **either** of two shapes — both are
valid input to `agents-cli eval generate`:

**Shape A — single-prompt case:**

```json
{
  "eval_cases": [
    {
      "eval_case_id": "unique_case_id",
      "prompt": {
        "role": "user",
        "parts": [{"text": "User message"}]
      }
    }
  ]
}
```

**Shape B — continued-conversation case (the "N+1" pattern):**
The case carries prior turns in `agent_data` and the last turn ends with a
user message; `eval generate` appends the next agent response.

```json
{
  "eval_cases": [
    {
      "eval_case_id": "unique_case_id",
      "agent_data": {
        "turns": [
          {
            "turn_index": 0,
            "events": [
              {"author": "user",  "content": {"role": "user",  "parts": [{"text": "First user message"}]}},
              {"author": "agent", "content": {"role": "model", "parts": [{"text": "First agent reply"}]}},
              {"author": "user",  "content": {"role": "user",  "parts": [{"text": "Follow-up user message"}]}}
            ]
          }
        ]
      }
    }
  ]
}
```

## Key Fields

- `eval_cases`: Array of evaluation cases.
- `eval_case_id`: Unique identifier for the evaluation case (optional).
- `prompt`: A single user message — Shape A.
- `agent_data.turns`: Prior conversation turns ending with a user message — Shape B.

## Creating Custom Datasets

You can create custom datasets in two ways:

1. **By Hand**: Copy `basic-dataset.json` as a template and manually add evaluation cases.
2. **Synthesize**: Use the synthetic dataset generation command to generate conversation scenarios:
   ```bash
   agents-cli eval dataset synthesize --count 10
   ```

## Discovering Metrics

You can discover available out-of-the-box evaluation metrics by running:

```bash
agents-cli eval metric list
```

## Beyond Generate and Grade

Once you have a baseline, the eval surface has a few more commands worth knowing about:

- `agents-cli eval compare BASE CAND` — diff two grade-results files (regression check).
- `agents-cli eval analyze RESULTS` — cluster failure modes from a grade-results file.
- `agents-cli eval optimize` — auto-tune your agent's prompts using eval data.

See the [Evaluation Guide](https://google.github.io/agents-cli/guide/evaluation/) for the full surface and metric reference.
