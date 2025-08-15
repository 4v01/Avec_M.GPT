from __future__ import annotations
from typing import Iterable, List, Tuple
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
class LogisticRegClassifier:
    def __init__(self) -> None:
        self.model = Pipeline([("tfidf", TfidfVectorizer(max_features=20000)), ("lr", LogisticRegression(max_iter=200))])
    def train(self, samples: Iterable[Tuple[str, int]]) -> None:
        X, y = zip(*samples) if samples else ([], [])
        if not X: return
        self.model.fit(list(X), list(y))
    def predict(self, texts: List[str]) -> List[int]:
        if not texts: return []
        try: return [int(x) for x in self.model.predict(texts)]
        except Exception: return [0]*len(texts)
