from __future__ import annotations
from typing import Dict, Iterable, Tuple, List
from sklearn.model_selection import train_test_split
from sklearn.metrics import precision_recall_fscore_support

from crawler_core.ml.model_selector import make_model

def train_and_eval(model_name: str, samples: Iterable[Tuple[str,int]], threshold: float = 0.7,
                   test_size: float = 0.2, random_state: int = 42) -> Dict[str, float|int|bool|str]:
    samples = list(samples)
    n = len(samples)
    if n < 20:
        return {"ok": False, "reason": "not-enough-samples", "n": n}

    X, y = zip(*samples)
    Xtr, Xte, ytr, yte = train_test_split(list(X), list(y), test_size=test_size, stratify=list(y), random_state=random_state)

    model = make_model(model_name)
    model.train(zip(Xtr, ytr))
    pred = model.predict(list(Xte))

    p,r,f1,_ = precision_recall_fscore_support(yte, pred, average="binary", zero_division=0)
    ok = f1 >= float(threshold)
    return {
        "ok": True,
        "precision": float(p), "recall": float(r), "f1": float(f1),
        "n_train": len(Xtr), "n_test": len(Xte),
        "pass_gate": ok
    }
