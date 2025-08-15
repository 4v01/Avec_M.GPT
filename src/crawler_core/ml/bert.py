from __future__ import annotations
from typing import Iterable, List, Tuple, Optional
try:
    from transformers import AutoTokenizer, AutoModelForSequenceClassification, TextClassificationPipeline
    _HF_OK = True
except Exception:
    _HF_OK = False
class BertClassifier:
    def __init__(self, model_name: str = "bert-base-chinese", num_labels: int = 2) -> None:
        self.available = False; self.pipe: Optional[TextClassificationPipeline] = None
        if not _HF_OK: return
        try:
            tok = AutoTokenizer.from_pretrained(model_name)
            mdl = AutoModelForSequenceClassification.from_pretrained(model_name, num_labels=num_labels)
            self.pipe = TextClassificationPipeline(model=mdl, tokenizer=tok, device=-1)  # CPU-only
            self.available = True
        except Exception:
            self.available = False; self.pipe = None
    def train(self, samples: Iterable[Tuple[str, int]]) -> None: return
    def predict(self, texts: List[str]) -> List[int]:
        if not self.available or not self.pipe: return [0]*len(texts)
        out: List[int] = []
        for t in texts:
            try:
                r = self.pipe(t, truncation=True, max_length=256)
                label = r[0]["label"]
                out.append(1 if str(label).upper().endswith("1") else 0)
            except Exception:
                out.append(0)
        return out
