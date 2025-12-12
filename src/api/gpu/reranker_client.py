"""Reranker client using HF Transformers (yes/no logits at last position)."""

import logging
from typing import Dict, List, Optional

import torch
import torch.nn.functional as F
from transformers import AutoModelForCausalLM, AutoTokenizer

logger = logging.getLogger(__name__)


class RerankerClient:
    """Client for reranking documents using HF reranker models (yes/no classifier)."""

    def __init__(
        self,
        model_repo: str,
        quantization: str,  # unused for HF; kept for interface compatibility
        n_ctx: int = 2048,  # unused; HF handles context
        n_gpu_layers: int = 0,  # unused; HF handles device
        rerank_instruction: str = (
            "Judge relevance for the task and concepts; respond yes if the document helps."
        ),
        debug_logprobs: bool = False,
    ):
        del quantization, n_ctx, n_gpu_layers  # Not used in HF backend
        # Map GGUF-style ids to HF ids if needed
        if model_repo.endswith("-GGUF"):
            self.model_repo = model_repo.replace("-GGUF", "")
        else:
            self.model_repo = model_repo
        self.rerank_instruction = rerank_instruction
        self.debug_logprobs = debug_logprobs

        try:
            logger.info("Loading HF reranker model: %s", self.model_repo)
            self.tokenizer = AutoTokenizer.from_pretrained(
                self.model_repo,
                padding_side="left",
                local_files_only=True,
            )
            self.model = AutoModelForCausalLM.from_pretrained(self.model_repo, local_files_only=True)

            if torch.backends.mps.is_available():
                self.device = torch.device("mps")
            elif torch.cuda.is_available():
                self.device = torch.device("cuda")
            else:
                self.device = torch.device("cpu")
            self.model.to(self.device)
            self.model.eval()

            # Include leading-space variants since the model is tokenized with spaces.
            self.yes_token_ids = [
                self.tokenizer.convert_tokens_to_ids(t)
                for t in ["yes", "Yes", " yes", " Yes"]
                if self.tokenizer.convert_tokens_to_ids(t) != self.tokenizer.unk_token_id
            ]
            self.no_token_ids = [
                self.tokenizer.convert_tokens_to_ids(t)
                for t in ["no", "No", " no", " No"]
                if self.tokenizer.convert_tokens_to_ids(t) != self.tokenizer.unk_token_id
            ]

            logger.info(
                "HF reranker loaded: %s on %s (yes ids=%s, no ids=%s)",
                self.model_repo,
                self.device,
                self.yes_token_ids,
                self.no_token_ids,
            )
        except Exception as exc:
            raise RerankerClientError(
                f"Failed to load HF reranker model '{self.model_repo}': {exc}"
            ) from exc

    def rerank(
        self,
        query: Dict[str, str],
        documents: List[str],
        batch_size: Optional[int] = None,
    ) -> List[float]:
        """
        Point-wise rerank using chat template and logits at the last position.

        Returns scores in [0,1] representing P(yes | instruction, query, document).
        """
        del batch_size  # Not used

        if not documents:
            return []

        try:
            logger.debug("Reranking %s documents with HF reranker", len(documents))
            scores: List[float] = []
            for doc in documents:
                prompt = self._build_prompt(query=query, document=doc)
                score = self._score_pair(prompt)
                scores.append(score)

            logger.debug("Reranked %s documents", len(documents))
            return scores
        except Exception as exc:
            raise RerankerClientError(f"Reranking failed: {exc}") from exc

    def _build_prompt(self, query: Dict[str, str], document: str) -> str:
        """Build chat prompt with explicit search/task components."""
        instruction = (self.rerank_instruction or "").strip()
        search_text = (query.get("search") or "").strip()
        task_text = (query.get("task") or "").strip()
        doc_text = (document or "").strip()
        max_chars = 4000
        if len(doc_text) > max_chars:
            doc_text = doc_text[:max_chars] + " ..."

        prefix = (
            "<|im_start|>system\n"
            'Judge whether the Document helps complete the Task given the Search concepts. '
            'Respond with a single token: "yes" or "no". Do not include <think>, punctuation, or explanations.<|im_end|>\n'
            "<|im_start|>user\n"
        )
        suffix = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"

        user_payload = (
            f"<Instruct>: {instruction}\n"
            f"<Search>: {search_text}\n"
            f"<Task>: {task_text}\n"
            f"<Document>: {doc_text}"
        )

        return prefix + user_payload + suffix

    def _score_pair(self, prompt: str) -> float:
        """Return P(yes) from logits at the last prompt position (no generation)."""
        inputs = self.tokenizer(
            prompt,
            return_tensors="pt",
            padding=False,
            truncation=True,
        )
        inputs = {k: v.to(self.device) for k, v in inputs.items()}

        with torch.no_grad():
            outputs = self.model(**inputs)
            logits = outputs.logits[:, -1, :]  # (1, vocab)

        # Take max over yes/no variants
        yes_logit = torch.max(logits[:, self.yes_token_ids], dim=1).values
        no_logit = torch.max(logits[:, self.no_token_ids], dim=1).values

        pair_logits = torch.stack([no_logit, yes_logit], dim=1)  # [1,2]
        probs = F.softmax(pair_logits, dim=1)
        return float(probs[:, 1].item())

    def get_model_version(self) -> str:
        return self.model_repo


class RerankerClientError(Exception):
    """Exception raised by RerankerClient."""

    pass
