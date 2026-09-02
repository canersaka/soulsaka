"""Plain transformers + peft + trl. Slower than Unsloth but runs anywhere CUDA (or, for a
tiny smoke test, a CPU)."""

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


class PeftBackend:
    name = "peft"

    def available(self) -> tuple[bool, str]:
        for mod in ("torch", "transformers", "peft", "trl", "datasets"):
            if importlib.util.find_spec(mod) is None:
                return False, f"{mod} is not installed (pip install -r requirements/train.txt)"
        return True, "ok"

    def train(self, dataset_dir: Path, out_dir: Path, cfg: TrainConfig, *, log) -> dict:
        import torch  # type: ignore
        from datasets import load_dataset  # type: ignore
        from peft import LoraConfig  # type: ignore
        from transformers import AutoModelForCausalLM, AutoTokenizer  # type: ignore
        from trl import SFTConfig, SFTTrainer  # type: ignore

        out_dir.mkdir(parents=True, exist_ok=True)
        cuda = torch.cuda.is_available()
        quant = None
        if cuda:
            try:
                from transformers import BitsAndBytesConfig  # type: ignore

                quant = BitsAndBytesConfig(
                    load_in_4bit=True,
                    bnb_4bit_quant_type="nf4",
                    bnb_4bit_compute_dtype=torch.bfloat16,
                    bnb_4bit_use_double_quant=True,
                )
            except Exception:  # noqa: BLE001
                quant = None
        tokenizer = AutoTokenizer.from_pretrained(cfg.base_model)
        if tokenizer.pad_token is None:
            tokenizer.pad_token = tokenizer.eos_token
        model = AutoModelForCausalLM.from_pretrained(
            cfg.base_model,
            quantization_config=quant,
            torch_dtype=torch.bfloat16 if cuda else torch.float32,
            device_map="auto" if cuda else None,
        )
        peft_config = LoraConfig(
            r=cfg.lora_r,
            lora_alpha=cfg.lora_alpha,
            lora_dropout=cfg.lora_dropout,
            target_modules=TARGET_MODULES,
            task_type="CAUSAL_LM",
        )
        data = load_dataset(
            "json",
            data_files={
                "train": str(dataset_dir / "train.jsonl"),
                "valid": str(dataset_dir / "valid.jsonl"),
            },
        )
        n_train = count_lines(dataset_dir / "train.jsonl")
        has_valid = count_lines(dataset_dir / "valid.jsonl") > 0
        instr, resp = chat_markers(cfg.base_model)
        args = SFTConfig(
            output_dir=str(out_dir / "checkpoints"),
            per_device_train_batch_size=cfg.batch_size,
            gradient_accumulation_steps=cfg.grad_accum,
            num_train_epochs=cfg.epochs,
            learning_rate=cfg.learning_rate,
            lr_scheduler_type="cosine",
            warmup_ratio=0.05,
            logging_steps=10,
            bf16=cuda,
            seed=cfg.seed,
            report_to="none",
            eval_strategy="epoch" if has_valid else "no",
            save_strategy="no",
            max_length=cfg.max_seq_len,
            packing=False,
            assistant_only_loss=False,
            completion_only_loss=None,
        )
        trainer = SFTTrainer(
            model=model,
            processing_class=tokenizer,
            train_dataset=data["train"],
            eval_dataset=data["valid"] if has_valid else None,
            args=args,
            peft_config=peft_config,
        )
        # Mask everything before the final assistant reply, like Unsloth does.
        try:
            from unsloth.chat_templates import train_on_responses_only  # type: ignore

            trainer = train_on_responses_only(trainer, instruction_part=instr, response_part=resp)
        except Exception:  # noqa: BLE001
            log("unsloth not available; training on full sequences (prompt tokens included)")
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
        trainer.model.save_pretrained(str(adapter_dir))
        tokenizer.save_pretrained(str(adapter_dir))
        write_metrics(out_dir, metrics)
        return metrics
