"""SLM Studio trainer entrypoint.

Runs one fine-tuning job described by a JSON spec taken from the environment:

  SLM_JOB_SPEC        JSON document (preferred, set by the finetune service on the K8s Job), or
  SLM_JOB_SPEC_FILE   path to a JSON file (e.g. a mounted ConfigMap/Secret)

Spec (all keys optional unless marked *):

  {
    "job_id": "<uuid>*", "org_id": "<uuid>", "name": "support-bot-v3",
    "method": "qlora" | "lora" | "full",                      # default qlora
    "base_model": {"uri": "hf://microsoft/Phi-4-mini-instruct" | "s3://models/<org>/<model>/<v>/"}*,
    "dataset":    {"uri": "s3://datasets/<org>/train.jsonl"*, "format": "jsonl|csv|parquet",
                   "eval_uri": "s3://...", "eval_split": 0.05},
    "hyperparams": {"learning_rate": 2e-4, "epochs": 3, "per_device_batch_size": 2,
                    "grad_accum": 8, "max_seq_len": 2048, "lora_r": 16, "lora_alpha": 32,
                    "lora_dropout": 0.0, "target_modules": [...], "warmup_ratio": 0.03,
                    "weight_decay": 0.01, "lr_scheduler": "cosine", "seed": 3407,
                    "logging_steps": 10, "save_steps": 200, "eval_steps": 200, "max_steps": -1},
    "output": {"artifact_uri": "s3://models/<org>/<job>/adapter"*,   # adapter (lora/qlora) or full weights
               "merged_uri": "s3://models/<org>/<job>/merged",       # optional merged 16-bit model
               "checkpoint_uri": "s3://checkpoints/<org>/<job>/"},   # optional; enables resume
    "mlflow": {"tracking_uri": "...", "experiment": "slm-studio", "run_name": "..."},
    "report": {"base_url": "http://slm-studio-finetune:8003",
               "metrics_path": "/internal/v1/training-jobs/{job_id}/metrics",
               "status_path":  "/internal/v1/training-jobs/{job_id}/status"},
    "s3": {"endpoint_url": "http://minio:9000", "region": "us-east-1"}
  }

Environment (credentials never travel in the spec):
  SLM_S3_ENDPOINT, SLM_S3_ACCESS_KEY, SLM_S3_SECRET_KEY, SLM_MLFLOW_URI, SLM_TRAINING_API_URL,
  SLM_TRAINER_TOKEN (bearer for the training API), HF_TOKEN, SLM_WANDB_* (optional, picked up by wandb)

Training-API contract used here (finetune service must expose; adjust paths in the spec if different):
  POST {metrics_path}  {"step","loss","eval_loss","lr","tokens","gpu_util"}
  POST {status_path}   {"status": "running|succeeded|failed", "mlflow_run_id", "tokens_processed",
                        "error", "artifact_uri", "merged_uri", "final_loss"}

Exit codes: 0 success | 1 training failed (reported) | 2 invalid spec (non-retryable)
            | 143 SIGTERM received: checkpoint saved + uploaded, no terminal status reported
              (the finetune service reconciles from the Job state; the Job may be retried/resumed).
"""

from __future__ import annotations

import dataclasses
import inspect
import json
import os
import signal
import sys
import time
import traceback
from pathlib import Path
from typing import Any

WORKDIR = Path(os.environ.get("SLM_TRAINER_WORKDIR", "/work"))
TERMINATION_LOG = Path("/dev/termination-log")

_stop_requested = False


def log(event: str, **fields: Any) -> None:
    print(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "event": event, **fields}, default=str), flush=True)


class SpecError(ValueError):
    pass


# --------------------------------------------------------------------------- spec
def load_spec() -> dict[str, Any]:
    raw = os.environ.get("SLM_JOB_SPEC")
    path = os.environ.get("SLM_JOB_SPEC_FILE")
    if not raw and path:
        raw = Path(path).read_text(encoding="utf-8")
    if not raw:
        raise SpecError("neither SLM_JOB_SPEC nor SLM_JOB_SPEC_FILE is set")
    try:
        spec = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SpecError(f"job spec is not valid JSON: {exc}") from exc

    for dotted in ("job_id", "base_model.uri", "dataset.uri", "output.artifact_uri"):
        cur: Any = spec
        for part in dotted.split("."):
            cur = cur.get(part) if isinstance(cur, dict) else None
        if not cur:
            raise SpecError(f"missing required spec key: {dotted}")
    spec.setdefault("method", "qlora")
    if spec["method"] not in ("lora", "qlora", "full"):
        raise SpecError(f"unsupported method: {spec['method']}")
    spec.setdefault("hyperparams", {})
    spec.setdefault("report", {})
    spec.setdefault("mlflow", {})
    spec.setdefault("s3", {})
    return spec


# ------------------------------------------------------------------------ reporting
class Reporter:
    """Best-effort reporting to the training API. Never raises: telemetry must not kill a run."""

    def __init__(self, spec: dict[str, Any]) -> None:
        rep = spec["report"]
        self.base = (rep.get("base_url") or os.environ.get("SLM_TRAINING_API_URL") or "").rstrip("/")
        job_id = spec["job_id"]
        self.metrics_url = self.base + rep.get("metrics_path", "/internal/v1/training-jobs/{job_id}/metrics").format(job_id=job_id)
        self.status_url = self.base + rep.get("status_path", "/internal/v1/training-jobs/{job_id}/status").format(job_id=job_id)
        token = os.environ.get("SLM_TRAINER_TOKEN", "")
        self.headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.enabled = bool(self.base)
        if not self.enabled:
            log("reporter_disabled", reason="no training API base_url / SLM_TRAINING_API_URL")

    def _post(self, url: str, payload: dict[str, Any]) -> None:
        if not self.enabled:
            return
        import httpx

        for attempt in range(3):
            try:
                r = httpx.post(url, json=payload, headers=self.headers, timeout=10)
                if r.status_code < 500:
                    if r.status_code >= 400:
                        log("report_rejected", url=url, status=r.status_code, body=r.text[:200])
                    return
            except Exception as exc:  # noqa: BLE001
                log("report_error", url=url, error=str(exc), attempt=attempt)
            time.sleep(2**attempt)

    def metrics(self, payload: dict[str, Any]) -> None:
        self._post(self.metrics_url, payload)

    def status(self, status: str, **extra: Any) -> None:
        self._post(self.status_url, {"status": status, **extra})


def gpu_util() -> float | None:
    try:
        import pynvml

        pynvml.nvmlInit()
        h = pynvml.nvmlDeviceGetHandleByIndex(0)
        return float(pynvml.nvmlDeviceGetUtilizationRates(h).gpu)
    except Exception:  # noqa: BLE001  (no NVML, or unified-memory SoC without it)
        return None


# ------------------------------------------------------------------------- storage
def _s3(spec: dict[str, Any]):
    import boto3
    from botocore.config import Config

    s3cfg = spec["s3"]
    return boto3.client(
        "s3",
        endpoint_url=s3cfg.get("endpoint_url") or os.environ.get("SLM_S3_ENDPOINT") or None,
        region_name=s3cfg.get("region") or os.environ.get("SLM_S3_REGION", "us-east-1"),
        aws_access_key_id=os.environ.get("SLM_S3_ACCESS_KEY") or None,
        aws_secret_access_key=os.environ.get("SLM_S3_SECRET_KEY") or None,
        config=Config(retries={"max_attempts": 5, "mode": "standard"}, s3={"addressing_style": "path"}),
    )


def _split_s3(uri: str) -> tuple[str, str]:
    assert uri.startswith("s3://"), uri
    bucket, _, key = uri[5:].partition("/")
    return bucket, key


def s3_download(spec: dict[str, Any], uri: str, dest: Path) -> Path:
    """Download an object (-> file) or a prefix (-> directory tree)."""
    client = _s3(spec)
    bucket, key = _split_s3(uri)
    keys: list[str] = []
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=key):
        keys += [o["Key"] for o in page.get("Contents", []) if not o["Key"].endswith("/")]
    if not keys:
        raise SpecError(f"nothing found at {uri}")
    if len(keys) == 1 and keys[0] == key:  # single object
        dest.parent.mkdir(parents=True, exist_ok=True)
        client.download_file(bucket, key, str(dest))
        return dest
    prefix = key if key.endswith("/") else key + "/"
    for k in keys:
        target = dest / k[len(prefix):]
        target.parent.mkdir(parents=True, exist_ok=True)
        client.download_file(bucket, k, str(target))
    return dest


def s3_upload_dir(spec: dict[str, Any], src: Path, uri: str) -> None:
    client = _s3(spec)
    bucket, key = _split_s3(uri)
    prefix = key.rstrip("/")
    for f in sorted(p for p in src.rglob("*") if p.is_file()):
        client.upload_file(str(f), bucket, f"{prefix}/{f.relative_to(src).as_posix()}")
    log("uploaded", uri=uri, files=sum(1 for _ in src.rglob("*") if _.is_file()))


def fetch_base_model(spec: dict[str, Any]) -> str:
    uri = spec["base_model"]["uri"]
    if uri.startswith("s3://"):
        return str(s3_download(spec, uri, WORKDIR / "base_model"))
    from huggingface_hub import snapshot_download

    repo = uri.removeprefix("hf://")
    return snapshot_download(repo_id=repo, token=os.environ.get("HF_TOKEN") or None)


def fetch_dataset(spec: dict[str, Any], uri: str, fmt: str | None, name: str):
    from datasets import load_dataset

    suffix = (fmt or Path(uri).suffix.lstrip(".") or "jsonl").lower()
    loader = {"jsonl": "json", "json": "json", "csv": "csv", "parquet": "parquet"}.get(suffix)
    if loader is None:
        raise SpecError(f"unsupported dataset format: {suffix}")
    local = s3_download(spec, uri, WORKDIR / "data" / f"{name}.{suffix}")
    return load_dataset(loader, data_files=str(local), split="train")


# ------------------------------------------------------------------------ training
def to_text_fn(tokenizer):
    """Normalise common dataset schemas to a single `text` column using the model's chat template."""

    def render(messages: list[dict[str, str]]) -> str:
        return tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=False)

    def fn(ex: dict[str, Any]) -> dict[str, str]:
        if ex.get("messages"):
            return {"text": render(ex["messages"])}
        if ex.get("prompt") is not None and (ex.get("completion") is not None or ex.get("response") is not None):
            return {"text": render([{"role": "user", "content": ex["prompt"]},
                                    {"role": "assistant", "content": ex.get("completion") or ex.get("response")}])}
        instruction = ex.get("instruction") if ex.get("instruction") is not None else ex.get("input")
        output = ex.get("output") if ex.get("output") is not None else ex.get("response")
        if instruction is not None and output is not None:  # alpaca / dolly / instruct-response
            context = ex.get("context") or (ex.get("input") if ex.get("instruction") is not None else None)
            user = instruction + (f"\n\n{context}" if context else "")
            return {"text": render([{"role": "user", "content": user}, {"role": "assistant", "content": output}])}
        if ex.get("text"):
            return {"text": ex["text"]}
        raise SpecError("dataset rows need one of: messages | prompt+completion | instruction+output | text")

    return fn


def build_callbacks(spec, reporter, mlflow_mod, state_box, upload_checkpoint):
    from transformers import TrainerCallback

    class StudioCallback(TrainerCallback):
        def on_log(self, args, state, control, logs=None, **kw):  # noqa: ANN001
            logs = logs or {}
            if "loss" not in logs and "eval_loss" not in logs:
                return
            payload = {
                "step": int(state.global_step),
                "loss": logs.get("loss"),
                "eval_loss": logs.get("eval_loss"),
                "lr": logs.get("learning_rate"),
                "tokens": int(getattr(state, "num_input_tokens_seen", 0) or 0),
                "gpu_util": gpu_util(),
            }
            state_box["tokens"] = payload["tokens"]
            if payload["loss"] is not None:
                state_box["final_loss"] = payload["loss"]
            reporter.metrics(payload)
            if mlflow_mod:
                try:
                    mlflow_mod.log_metrics({k: v for k, v in logs.items() if isinstance(v, (int, float))}, step=state.global_step)
                except Exception as exc:  # noqa: BLE001
                    log("mlflow_error", error=str(exc))

        def on_save(self, args, state, control, **kw):  # noqa: ANN001
            upload_checkpoint(Path(args.output_dir) / f"checkpoint-{state.global_step}")

        def on_step_end(self, args, state, control, **kw):  # noqa: ANN001
            if _stop_requested:
                control.should_save = True
                control.should_training_stop = True

    return [StudioCallback()]


def run(spec: dict[str, Any], reporter: Reporter) -> dict[str, Any]:
    method = spec["method"]
    hp = spec["hyperparams"]
    max_seq_len = int(hp.get("max_seq_len", 2048))
    seed = int(hp.get("seed", 3407))
    out_dir = WORKDIR / "output"
    ckpt_dir = WORKDIR / "checkpoints"
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    state_box: dict[str, Any] = {"tokens": 0, "final_loss": None}

    # --- MLflow ---------------------------------------------------------------
    mlflow_mod = None
    run_id = None
    tracking = spec["mlflow"].get("tracking_uri") or os.environ.get("SLM_MLFLOW_URI")
    if tracking:
        try:
            import mlflow

            mlflow.set_tracking_uri(tracking)
            mlflow.set_experiment(spec["mlflow"].get("experiment", "slm-studio"))
            active = mlflow.start_run(run_name=spec["mlflow"].get("run_name") or spec.get("name") or spec["job_id"])
            run_id = active.info.run_id
            mlflow.set_tags({"job_id": spec["job_id"], "org_id": spec.get("org_id", ""), "method": method})
            mlflow.log_params({"base_model": spec["base_model"]["uri"], "dataset": spec["dataset"]["uri"],
                               "method": method, **{f"hp.{k}": v for k, v in hp.items() if not isinstance(v, (list, dict))}})
            mlflow_mod = mlflow
        except Exception as exc:  # noqa: BLE001
            log("mlflow_disabled", error=str(exc))
    reporter.status("running", mlflow_run_id=run_id)

    # --- model + tokenizer ----------------------------------------------------
    model_path = fetch_base_model(spec)
    if method in ("lora", "qlora"):
        try:
            from unsloth import FastLanguageModel  # must be imported before transformers/peft/trl

            model, tokenizer = FastLanguageModel.from_pretrained(
                model_name=model_path, max_seq_length=max_seq_len, dtype=None, load_in_4bit=(method == "qlora"),
                token=os.environ.get("HF_TOKEN") or None,
            )
            model = FastLanguageModel.get_peft_model(
                model, r=int(hp.get("lora_r", 16)), lora_alpha=int(hp.get("lora_alpha", 32)),
                lora_dropout=float(hp.get("lora_dropout", 0.0)), bias="none",
                target_modules=hp.get("target_modules") or ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
                use_gradient_checkpointing="unsloth", random_state=seed,
            )
        except Exception as unsloth_err:
            log("unsloth_fallback", error=str(unsloth_err))
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
            from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training

            tokenizer = AutoTokenizer.from_pretrained(model_path)
            bnb_config = None
            if method == "qlora":
                bnb_config = BitsAndBytesConfig(
                    load_in_4bit=True,
                    bnb_4bit_quant_type="nf4",
                    bnb_4bit_compute_dtype=torch.bfloat16,
                    bnb_4bit_use_double_quant=True,
                )
            model = AutoModelForCausalLM.from_pretrained(
                model_path,
                quantization_config=bnb_config,
                torch_dtype=torch.bfloat16 if method != "qlora" else None,
                device_map="auto",
            )
            if method == "qlora":
                model = prepare_model_for_kbit_training(model)
            lora_config = LoraConfig(
                r=int(hp.get("lora_r", 16)),
                lora_alpha=int(hp.get("lora_alpha", 32)),
                lora_dropout=float(hp.get("lora_dropout", 0.0)),
                bias="none",
                task_type="CAUSAL_LM",
                target_modules=hp.get("target_modules") or ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
            )
            model = get_peft_model(model, lora_config)
            model.gradient_checkpointing_enable()
    else:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(model_path)
        model = AutoModelForCausalLM.from_pretrained(model_path, torch_dtype=torch.bfloat16)
        model.gradient_checkpointing_enable()

    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    # --- data -----------------------------------------------------------------
    ds_spec = spec["dataset"]
    train_ds = fetch_dataset(spec, ds_spec["uri"], ds_spec.get("format"), "train")
    eval_ds = None
    if ds_spec.get("eval_uri"):
        eval_ds = fetch_dataset(spec, ds_spec["eval_uri"], ds_spec.get("format"), "eval")
    elif float(ds_spec.get("eval_split", 0) or 0) > 0:
        parts = train_ds.train_test_split(test_size=float(ds_spec["eval_split"]), seed=seed)
        train_ds, eval_ds = parts["train"], parts["test"]
    fn = to_text_fn(tokenizer)
    train_ds = train_ds.map(fn, remove_columns=train_ds.column_names)
    if eval_ds is not None:
        eval_ds = eval_ds.map(fn, remove_columns=eval_ds.column_names)

    # --- checkpoints: resume if a previous attempt uploaded one ----------------
    ck_uri = spec["output"].get("checkpoint_uri")

    def upload_checkpoint(path: Path) -> None:
        if ck_uri and path.exists():
            try:
                s3_upload_dir(spec, path, ck_uri.rstrip("/") + "/" + path.name)
            except Exception as exc:  # noqa: BLE001
                log("checkpoint_upload_failed", error=str(exc))

    resume_from = None
    if ck_uri:
        try:
            client = _s3(spec)
            bucket, key = _split_s3(ck_uri.rstrip("/") + "/")
            steps = set()
            for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=key):
                for o in page.get("Contents", []):
                    head = o["Key"][len(key):].split("/", 1)[0]
                    if head.startswith("checkpoint-") and head[11:].isdigit():
                        steps.add(int(head[11:]))
            if steps:
                latest = f"checkpoint-{max(steps)}"
                s3_download(spec, ck_uri.rstrip("/") + "/" + latest + "/", ckpt_dir / latest)
                resume_from = str(ckpt_dir / latest)
                log("resuming", checkpoint=resume_from)
        except Exception as exc:  # noqa: BLE001
            log("resume_skipped", error=str(exc))

    # --- trainer ---------------------------------------------------------------
    from trl import SFTConfig, SFTTrainer

    cfg_fields = {f.name for f in dataclasses.fields(SFTConfig)}
    kwargs: dict[str, Any] = dict(
        output_dir=str(ckpt_dir),
        num_train_epochs=float(hp.get("epochs", 3)),
        max_steps=int(hp.get("max_steps", -1)),
        per_device_train_batch_size=int(hp.get("per_device_batch_size", 2)),
        gradient_accumulation_steps=int(hp.get("grad_accum", 8)),
        learning_rate=float(hp.get("learning_rate", 2e-4)),
        warmup_ratio=float(hp.get("warmup_ratio", 0.03)),
        weight_decay=float(hp.get("weight_decay", 0.01)),
        lr_scheduler_type=hp.get("lr_scheduler", "cosine"),
        logging_steps=int(hp.get("logging_steps", 10)),
        save_steps=int(hp.get("save_steps", 200)),
        save_total_limit=2,
        seed=seed,
        bf16=True,
        optim="adamw_8bit" if method == "qlora" else "adamw_torch",
        dataset_text_field="text",
        report_to="none",
        include_num_input_tokens_seen=True,
    )
    kwargs["max_length" if "max_length" in cfg_fields else "max_seq_length"] = max_seq_len
    if eval_ds is not None:
        kwargs["eval_steps"] = int(hp.get("eval_steps", hp.get("save_steps", 200)))
        kwargs["eval_strategy" if "eval_strategy" in cfg_fields else "evaluation_strategy"] = "steps"
    sft_args = SFTConfig(**{k: v for k, v in kwargs.items() if k in cfg_fields})

    tok_kw = "processing_class" if "processing_class" in inspect.signature(SFTTrainer.__init__).parameters else "tokenizer"
    trainer = SFTTrainer(
        model=model, args=sft_args, train_dataset=train_ds, eval_dataset=eval_ds,
        callbacks=build_callbacks(spec, reporter, mlflow_mod, state_box, upload_checkpoint),
        **{tok_kw: tokenizer},
    )
    trainer.train(resume_from_checkpoint=resume_from)

    if _stop_requested:
        log("sigterm_checkpointed")
        return {"interrupted": True}

    # --- artifacts ---------------------------------------------------------------
    out_dir.mkdir(parents=True, exist_ok=True)
    adapter_dir = out_dir / "artifact"
    trainer.save_model(str(adapter_dir))
    tokenizer.save_pretrained(str(adapter_dir))
    s3_upload_dir(spec, adapter_dir, spec["output"]["artifact_uri"])
    result: dict[str, Any] = {"artifact_uri": spec["output"]["artifact_uri"], "mlflow_run_id": run_id,
                              "tokens_processed": state_box["tokens"], "final_loss": state_box["final_loss"]}
    merged_uri = spec["output"].get("merged_uri")
    if merged_uri and method in ("lora", "qlora"):
        merged_dir = out_dir / "merged"
        if hasattr(model, "save_pretrained_merged"):
            model.save_pretrained_merged(str(merged_dir), tokenizer, save_method="merged_16bit")  # unsloth API
        else:
            merged = model.merge_and_unload()
            merged.save_pretrained(str(merged_dir))
            tokenizer.save_pretrained(str(merged_dir))
        s3_upload_dir(spec, merged_dir, merged_uri)
        result["merged_uri"] = merged_uri
    if mlflow_mod:
        try:
            if state_box["final_loss"] is not None:
                mlflow_mod.log_metric("final_loss", state_box["final_loss"])
            mlflow_mod.end_run("FINISHED")
        except Exception as exc:  # noqa: BLE001
            log("mlflow_error", error=str(exc))
    return result


def write_termination_message(payload: dict[str, Any]) -> None:
    try:
        TERMINATION_LOG.write_text(json.dumps(payload)[:4000], encoding="utf-8")
    except OSError:
        pass


def _on_sigterm(signum, frame) -> None:  # noqa: ANN001
    global _stop_requested
    _stop_requested = True
    log("sigterm_received")


def main() -> int:
    signal.signal(signal.SIGTERM, _on_sigterm)
    WORKDIR.mkdir(parents=True, exist_ok=True)
    try:
        spec = load_spec()
    except SpecError as exc:
        log("invalid_spec", error=str(exc))
        write_termination_message({"error": str(exc), "retryable": False})
        return 2

    reporter = Reporter(spec)
    log("job_start", job_id=spec["job_id"], method=spec["method"])
    try:
        result = run(spec, reporter)
    except SpecError as exc:
        log("invalid_spec", error=str(exc))
        reporter.status("failed", error=str(exc))
        write_termination_message({"error": str(exc), "retryable": False})
        return 2
    except Exception as exc:  # noqa: BLE001
        log("job_failed", error=str(exc), trace=traceback.format_exc()[-3000:])
        reporter.status("failed", error=f"{type(exc).__name__}: {exc}"[:1000])
        write_termination_message({"error": str(exc)[:1000]})
        return 1

    if result.get("interrupted"):
        return 143
    reporter.status("succeeded", **result)
    write_termination_message(result)
    log("job_succeeded", **result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
