from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any
from uuid import UUID

import asyncpg
import httpx
import structlog

from slm_common.db import tenant_tx
from slm_evaluation.config import EvaluationSettings
from slm_evaluation.judge import LLMJudge
from slm_evaluation.metrics import SUPPORTED_METRICS, compute_metrics, normalize_text
from slm_evaluation.s3 import download_dataset_rows

logger = structlog.get_logger(__name__)

LLM_JUDGED_METRICS = {"faithfulness", "groundedness", "hallucination", "toxicity"}


def extract_prompt_and_ref(row: dict[str, Any]) -> tuple[str, str]:
    """Extract (prompt, reference_output) from varying dataset schemas."""
    # 1. messages format
    if "messages" in row and isinstance(row["messages"], list):
        msgs = row["messages"]
        user_msgs = [m.get("content", "") for m in msgs if m.get("role") == "user"]
        assistant_msgs = [m.get("content", "") for m in msgs if m.get("role") == "assistant"]
        prompt = "\n".join(user_msgs) if user_msgs else (msgs[0].get("content", "") if msgs else "")
        ref = assistant_msgs[-1] if assistant_msgs else (msgs[-1].get("content", "") if msgs else "")
        return prompt or "Hello", ref or "Hello"

    # 2. prompt + completion / response
    if "prompt" in row:
        prompt = str(row["prompt"])
        ref = str(row.get("completion") or row.get("response") or row.get("output") or "")
        return prompt, ref

    # 3. instruction + output (alpaca)
    if "instruction" in row:
        prompt = str(row["instruction"])
        if row.get("input"):
            prompt += f"\n\n{row['input']}"
        ref = str(row.get("output") or "")
        return prompt, ref

    # 4. text format
    if "text" in row:
        t = str(row["text"])
        return t, t

    # 5. question / answer
    if "question" in row:
        return str(row["question"]), str(row.get("answer") or "")

    return "", ""


async def _infer_single(
    sem: asyncio.Semaphore,
    client: httpx.AsyncClient,
    endpoint: str,
    model_name: str,
    prompt: str,
) -> tuple[str, float, int, int, bool]:
    """Execute inference for a single prompt under semaphore concurrency."""
    async with sem:
        t0 = time.perf_counter()
        try:
            resp = await client.post(
                f"{endpoint}/v1/chat/completions",
                json={
                    "model": model_name,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 256,
                    "temperature": 0.0,
                },
            )
            t1 = time.perf_counter()
            lat_ms = (t1 - t0) * 1000.0
            if resp.status_code == 200:
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                usage = data.get("usage", {})
                p_tokens = usage.get("prompt_tokens") or len(prompt.split())
                c_tokens = usage.get("completion_tokens") or len(content.split())
                return content, lat_ms, p_tokens, c_tokens, True
            logger.warning("inference_non_200", status=resp.status_code, body=resp.text[:150])
            return "", lat_ms, 0, 0, False
        except Exception as exc:
            t1 = time.perf_counter()
            lat_ms = (t1 - t0) * 1000.0
            logger.warning("inference_exception", error=str(exc))
            return "", lat_ms, 0, 0, False


async def _resolve_served_model_name(client: httpx.AsyncClient, endpoint: str, requested_name: str) -> str:
    """Detect the exact model ID served by the endpoint (e.g. vLLM instance)."""
    try:
        resp = await client.get(f"{endpoint}/v1/models", timeout=5.0)
        if resp.status_code == 200:
            data = resp.json().get("data", [])
            served_ids = [m.get("id") for m in data if isinstance(m, dict) and "id" in m]
            if requested_name in served_ids:
                return requested_name
            for sid in served_ids:
                if sid.lower() == requested_name.lower():
                    return sid
            if len(served_ids) == 1:
                logger.info("using_single_served_model", requested=requested_name, served=served_ids[0])
                return served_ids[0]
    except Exception as exc:
        logger.warning("failed_to_query_models_endpoint", error=str(exc))
    return requested_name


class EvaluationEngine:
    def __init__(self, settings: EvaluationSettings) -> None:
        self.settings = settings

    async def run_evaluation(self, evaluation_id: UUID, org_id: UUID, pool: asyncpg.Pool) -> None:
        """Run evaluation job end-to-end and persist results."""
        logger.info("evaluation_started", evaluation_id=str(evaluation_id))
        async with tenant_tx(pool, org_id) as conn:
            await conn.execute(
                "UPDATE evaluations SET status = 'running' WHERE id = $1 AND org_id = $2 AND status = 'queued'",
                evaluation_id,
                org_id,
            )
            eval_row = await conn.fetchrow(
                """
                SELECT e.*, d.storage_uri, d.format AS dataset_format, d.name AS dataset_name
                FROM evaluations e
                JOIN datasets d ON e.dataset_id = d.id
                WHERE e.id = $1 AND e.org_id = $2
                """,
                evaluation_id,
                org_id,
            )

        if not eval_row:
            logger.error("evaluation_not_found", evaluation_id=str(evaluation_id))
            return

        try:
            # 1. Download dataset rows - fail fast if empty or missing
            storage_uri = eval_row["storage_uri"]
            dataset_rows: list[dict[str, Any]] = download_dataset_rows(storage_uri, self.settings)
            if not dataset_rows:
                raise ValueError(f"Dataset at '{storage_uri}' is empty or contained no valid records")

            samples = [extract_prompt_and_ref(r) for r in dataset_rows]
            samples = [s for s in samples if s[0]]  # filter empty prompts
            if not samples:
                raise ValueError(f"Dataset at '{storage_uri}' contains no valid (prompt, reference) pairs")

            prompts = [s[0] for s in samples]
            references = [s[1] for s in samples]

            requested_metrics = list(eval_row["metrics"])
            judge_model = eval_row["judge_model"]
            model_version_ids: list[UUID] = list(eval_row["model_version_ids"])

            # 2. Evaluate each model version
            for mv_id in model_version_ids:
                async with tenant_tx(pool, org_id) as conn:
                    mv_row = await conn.fetchrow(
                        """
                        SELECT mv.id, mv.artifact_uri, mv.param_count, mv.format, m.name, mv.version
                        FROM model_versions mv
                        JOIN models m ON mv.model_id = m.id
                        WHERE mv.id = $1
                        """,
                        mv_id,
                    )
                    dep_row = await conn.fetchrow(
                        """
                        SELECT internal_url
                        FROM deployments
                        WHERE model_version_id = $1 AND status = 'running'
                        LIMIT 1
                        """,
                        mv_id,
                    )

                model_name = mv_row["name"] if mv_row else "model"
                endpoint = (dep_row["internal_url"] if dep_row and dep_row["internal_url"] else self.settings.vllm_url).rstrip("/")

                sem = asyncio.Semaphore(4)  # concurrency 4 per MLOps spec
                async with httpx.AsyncClient(timeout=30.0) as http_client:
                    served_model = await _resolve_served_model_name(http_client, endpoint, model_name)
                    tasks = [
                        _infer_single(sem, http_client, endpoint, served_model, p)
                        for p in prompts
                    ]
                    results = await asyncio.gather(*tasks)

                predictions = [r[0] for r in results]
                latencies = [r[1] for r in results if r[4]]  # only successful calls
                total_p_tokens = sum(r[2] for r in results)
                total_c_tokens = sum(r[3] for r in results)
                success_count = sum(1 for r in results if r[4])

                if success_count == 0:
                    raise RuntimeError(
                        f"Model '{model_name}' failed inference on all {len(samples)} samples at {endpoint}. "
                        "Check that the model deployment is active and healthy."
                    )

                latencies.sort()
                n_lat = len(latencies)
                p50_ms = int(latencies[n_lat // 2]) if n_lat else None
                p95_ms = int(latencies[int(n_lat * 0.95)]) if n_lat else None

                # 3. Compute Metrics
                scores: dict[str, float] = {}

                # Compute standard text metrics
                std_names = [m for m in requested_metrics if m in SUPPORTED_METRICS]
                if std_names:
                    try:
                        std_scores = compute_metrics(std_names, predictions, references)
                        scores.update(std_scores)
                    except Exception as err:
                        logger.warning("compute_metrics_error", error=str(err))

                # Handle LLM-judged metrics
                judged_requested = [m for m in requested_metrics if m in LLM_JUDGED_METRICS]
                if judged_requested:
                    judge_endpoint = getattr(self.settings, "judge_url", None) or self.settings.vllm_url
                    judge = LLMJudge(
                        judge_model=judge_model or model_name,
                        endpoint_url=judge_endpoint,
                    )

                    faith_list: list[float] = []
                    grounded_list: list[float] = []
                    halluc_list: list[float] = []
                    tox_list: list[float] = []

                    needs_claims = any(m in ("faithfulness", "groundedness", "hallucination") for m in judged_requested)
                    needs_tox = "toxicity" in judged_requested

                    for p, r, pred in zip(prompts, references, predictions):
                        if needs_claims:
                            f, g, h = await judge.evaluate_faithfulness_and_hallucination(p, pred, r)
                            faith_list.append(f)
                            grounded_list.append(g)
                            halluc_list.append(h)
                        if needs_tox:
                            tox = await judge.evaluate_toxicity(pred)
                            tox_list.append(tox)

                    if "faithfulness" in judged_requested and faith_list:
                        scores["faithfulness"] = round(sum(faith_list) / len(faith_list), 3)
                    if "groundedness" in judged_requested and grounded_list:
                        scores["groundedness"] = round(sum(grounded_list) / len(grounded_list), 3)
                    if "hallucination" in judged_requested and halluc_list:
                        scores["hallucination"] = round(sum(halluc_list) / len(halluc_list), 3)
                    if "toxicity" in judged_requested and tox_list:
                        scores["toxicity"] = round(sum(tox_list) / len(tox_list), 3)

                # Cost per 1K tokens calculation
                total_tokens = total_p_tokens + total_c_tokens
                if total_tokens > 0:
                    # Blended token cost per 1K tokens ($0.00015 input, $0.00060 output)
                    total_usd = (total_p_tokens * 0.00015 / 1000) + (total_c_tokens * 0.00060 / 1000)
                    cost_per_1k = round((total_usd / total_tokens) * 1000, 6)
                else:
                    param_count = (mv_row["param_count"] if mv_row else 7_000_000_000) or 7_000_000_000
                    cost_per_1k = round(max(0.00005, (param_count / 1e9) * 0.000025), 6)

                # 4. Save result
                async with tenant_tx(pool, org_id) as conn:
                    await conn.execute(
                        """
                        INSERT INTO evaluation_results (
                            evaluation_id, org_id, model_version_id, scores,
                            latency_p50_ms, latency_p95_ms, cost_per_1k_tokens_usd, sample_count
                        )
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                        ON CONFLICT (evaluation_id, model_version_id) DO UPDATE SET
                            scores = EXCLUDED.scores,
                            latency_p50_ms = EXCLUDED.latency_p50_ms,
                            latency_p95_ms = EXCLUDED.latency_p95_ms,
                            cost_per_1k_tokens_usd = EXCLUDED.cost_per_1k_tokens_usd,
                            sample_count = EXCLUDED.sample_count
                        """,
                        evaluation_id,
                        org_id,
                        mv_id,
                        json.dumps(scores),
                        p50_ms,
                        p95_ms,
                        cost_per_1k,
                        len(samples),
                    )

                    # Update benchmark_scores on model version
                    await conn.execute(
                        """
                        UPDATE model_versions
                        SET benchmark_scores = COALESCE(
                            CASE
                                WHEN jsonb_typeof(benchmark_scores) = 'object' THEN benchmark_scores
                                ELSE '{}'::jsonb
                            END,
                            '{}'::jsonb
                        ) || ($1::text)::jsonb
                        WHERE id = $2
                        """,
                        json.dumps(scores),
                        mv_id,
                    )

            # 5. Mark evaluation succeeded
            async with tenant_tx(pool, org_id) as conn:
                await conn.execute(
                    """
                    UPDATE evaluations
                    SET status = 'succeeded', finished_at = now()
                    WHERE id = $1 AND org_id = $2
                    """,
                    evaluation_id,
                    org_id,
                )
            logger.info("evaluation_succeeded", evaluation_id=str(evaluation_id))

        except Exception as exc:
            logger.exception("evaluation_failed", evaluation_id=str(evaluation_id), error=str(exc))
            async with tenant_tx(pool, org_id) as conn:
                await conn.execute(
                    """
                    UPDATE evaluations
                    SET status = 'failed', finished_at = now()
                    WHERE id = $1 AND org_id = $2
                    """,
                    evaluation_id,
                    org_id,
                )
