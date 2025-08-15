from __future__ import annotations
from typing import Any, Dict
from crawler_core.ml.naive_bayes import NaiveBayesClassifier
from crawler_core.ml.logistic_regression import LogisticRegClassifier
try:
    from crawler_core.ml.bert import BertClassifier
except Exception:
    BertClassifier = None  # type: ignore
MODEL_REGISTRY: Dict[str, Any] = {"nb": NaiveBayesClassifier, "lr": LogisticRegClassifier, "bert": BertClassifier}
def make_model(name: str):
    name = (name or "nb").lower()
    cls = MODEL_REGISTRY.get(name) or NaiveBayesClassifier
    return cls() if cls is not None else NaiveBayesClassifier()
