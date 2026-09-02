"""Unsloth QLoRA on CUDA (the G14 path)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from soulsaka.config import TrainConfig
from soulsaka.train.backends.common import (
    TARGET_MODULES,
    Timer,
    chat_markers,
    count_lines,
    steps_for,
    write_metrics,
)


class UnslothBackend:
    name = "unsloth"

    def available(self) -> tuple[bool, str]:
        if importlib.util.find_spec("unsloth") is None:
            return False, "unsloth is not installed (pip install -r requirements/train.txt)"
        try:
            import torch  # type: ignore

            if not torch.cuda.is_available():
                return False, "no CUDA device"
        except Exception as e:  # noqa: BLE001
            return False, f"torch unavailable: {e}"
        return True, "ok"

    def train(self, dataset_dir: Path, out_dir: Path, cfg: TrainConfig, *, log) -> dict:
        from datasets import load_dataset  # type: ignore
        from trl import SFTConfig, SFTTrainer  # type: ignore
        from unsloth import FastLanguageModel, is_bfloat16_supported  # type: ignore
        from unsloth.chat_templates import train_on_responses_only  # type: ignore

        out_dir.mkdir(parents=True, exist_ok=True)
        model, tokenizer = FastLanguageModel.from_pretrained(
            model_name=cfg.base_model,
            max_seq_length=cfg.max_seq_len,
            load_in_4bit=True,
            dtype=None,
        )
        model = FastLanguageModel.get_peft_model(
            model,
            r=cfg.lora_r,
            target_modules=TARGET_MODULES,
            lora_alpha=cfg.lora_alpha,
            lora_dropout=cfg.lora_dropout,
            bias="none",
            use_gradient_checkpointing="unsloth",
            random_state=cfg.seed,
        )
        data = load_dataset(
            "json",
            data_files={
                "train": str(dataset_dir / "train.jsonl"),
                "valid": str(dataset_dir / "valid.jsonl"),
            },
        )

        def to_text(batch):
            texts = [
                tokenizer.apply_chat_template(m, tokenize=False, add_generation_prompt=False)
                for m in batch["messages"]
            ]
            return {"text": texts}

        data = data.map(
            to_text,
            batched=True,
            remove_columns=[c for c in data["train"].column_names if c != "text"],
        )
        n_train = count_lines(dataset_dir / "train.jsonl")
        has_valid = count_lines(dataset_dir / "valid.jsonl") > 0
        args = SFTConfig(
            output_dir=str(out_dir / "checkpoints"),
            per_device_train_batch_size=cfg.batch_size,
            gradient_accumulation_steps=cfg.grad_accum,
            num_train_epochs=cfg.epochs,
            learning_rate=cfg.learning_rate,
            lr_scheduler_type="cosine",
            warmup_ratio=0.05,
            logging_steps=10,
            bf16=is_bfloat16_supported(),
            fp16=not is_bfloat16_supported(),
            optim="adamw_8bit",
            weight_decay=0.01,
            seed=cfg.seed,
            report_to="none",
            eval_strategy="epoch" if has_valid else "no",
            save_strategy="no",
            dataset_text_field="text",
            max_seq_length=cfg.max_seq_len,
            packing=False,
        )
        trainer = SFTTrainer(
            model=model,
            tokenizer=tokenizer,
            train_dataset=data["train"],
            eval_dataset=data["valid"] if has_valid else None,
            args=args,
        )
        instr, resp = chat_markers(cfg.base_model)
        trainer = train_on_responses_only(trainer, instruction_part=instr, response_part=resp)
        log(f"training {n_train} examples for ~{steps_for(cfg, n_train)} steps on {cfg.base_model}")
        with Timer() as t:
            result = trainer.train()
        metrics = {
            "train_loss": float(result.training_loss),
            "steps": int(result.global_step),
            "wall_s": round(t.seconds, 1),
        }
        if has_valid:
            ev = trainer.evaluate()
            metrics["eval_loss"] = float(ev.get("eval_loss", float("nan")))
        adapter_dir = out_dir / "adapter"
        model.save_pretrained(str(adapter_dir))
        tokenizer.save_pretrained(str(adapter_dir))
        write_metrics(out_dir, metrics)
        return metrics
