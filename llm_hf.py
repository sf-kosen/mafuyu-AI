# HuggingFace LLM Backend (for LoRA usage)
# 将来的にLoRAを使う場合はこちらを使う

import torch
from pathlib import Path
from typing import Optional

# Will be imported when actually used
# from transformers import AutoTokenizer, AutoModelForCausalLM, BitsAndBytesConfig
# from peft import PeftModel

from config import BASE_DIR


class HuggingFaceLLM:
    """
    HuggingFace backend for Mafuyu.
    Supports LoRA adapters and quantization.
    
    Usage:
        llm = HuggingFaceLLM(
            model_id="google/gemma-3-4b-it",
            adapter_dir="outputs/gemma3-4b-lora",  # Optional
            load_4bit=True  # Recommended for RTX 3070
        )
        response = llm.generate(messages)
    """
    
    def __init__(
        self,
        model_id: str = "google/gemma-3-4b-it",
        adapter_dir: Optional[str] = None,
        load_4bit: bool = True,
        load_8bit: bool = False,
        device_map: str = "auto",
    ):
        self.model_id = model_id
        self.adapter_dir = adapter_dir
        self.model = None
        self.tokenizer = None
        self.load_4bit = load_4bit
        self.load_8bit = load_8bit
        self.device_map = device_map
    
    def load(self):
        """Load model and tokenizer."""
        from transformers import AutoTokenizer, AutoModelForCausalLM, BitsAndBytesConfig
        from peft import PeftModel
        
        print(f"[HF] Loading {self.model_id}...")
        
        # Quantization config
        quant = None
        if self.load_4bit:
            quant = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_use_double_quant=True,
                bnb_4bit_compute_dtype=torch.float16,
            )
        elif self.load_8bit:
            quant = BitsAndBytesConfig(load_in_8bit=True)
        
        # Load tokenizer
        self.tokenizer = AutoTokenizer.from_pretrained(self.model_id)
        
        # Load model
        model_kwargs = {
            "device_map": self.device_map,
            "low_cpu_mem_usage": True,
        }
        if quant:
            model_kwargs["quantization_config"] = quant
        else:
            model_kwargs["torch_dtype"] = torch.float16
        
        self.model = AutoModelForCausalLM.from_pretrained(
            self.model_id, **model_kwargs
        )
        
        # Load LoRA adapter if specified
        if self.adapter_dir:
            adapter_path = Path(self.adapter_dir)
            if adapter_path.exists():
                print(f"[HF] Loading LoRA: {self.adapter_dir}")
                self.model = PeftModel.from_pretrained(self.model, self.adapter_dir)
            else:
                print(f"[HF] Warning: Adapter not found: {self.adapter_dir}")
        
        self.model.eval()
        print("[HF] Model loaded!")
    
    def generate(
        self,
        messages: list[dict],
        max_new_tokens: int = 256,
        temperature: float = 0.7,
        top_p: float = 0.9,
        top_k: int = 50,
    ) -> str:
        """
        Generate response from messages.
        
        Args:
            messages: List of {"role": "...", "content": "..."}
            max_new_tokens: Max tokens to generate
            temperature: Sampling temperature
            top_p: Nucleus sampling
            top_k: Top-k sampling
        
        Returns:
            Generated text
        """
        if self.model is None:
            self.load()
        
        # Apply chat template
        input_ids = self.tokenizer.apply_chat_template(
            messages,
            return_tensors="pt",
            add_generation_prompt=True,
        ).to(self.model.device)
        
        # Generate
        with torch.no_grad():
            output = self.model.generate(
                input_ids=input_ids,
                max_new_tokens=max_new_tokens,
                do_sample=True,
                temperature=temperature,
                top_p=top_p,
                top_k=top_k,
            )
        
        # Decode new tokens only
        new_tokens = output[0][input_ids.shape[-1]:]
        return self.tokenizer.decode(new_tokens, skip_special_tokens=True).strip()


# ============ Backend Switching ============

# Set this to switch between backends
LLM_BACKEND = "ollama"  # "ollama" or "huggingface"

_hf_llm = None

def call_llm(messages: list[dict]) -> str:
    """
    Universal LLM call that works with either backend.
    """
    global _hf_llm
    
    if LLM_BACKEND == "huggingface":
        if _hf_llm is None:
            _hf_llm = HuggingFaceLLM(
                model_id="google/gemma-3-4b-it",
                adapter_dir=str(BASE_DIR / "outputs" / "gemma3-4b-lora"),
                load_4bit=True,
            )
            _hf_llm.load()
        return _hf_llm.generate(messages)
    else:
        # Default: Ollama
        from llm import call_ollama
        return call_ollama(messages)
