# -*- coding: utf-8 -*-
"""
meta_policy.py — Router par régime
"""

from __future__ import annotations
from typing import Dict, Tuple
import numpy as np

class RegimeDetector:
    def detect(self, features: Dict) -> str:
        atrp = float(features.get("atr_pct", 0.0))
        adx  = float(features.get("adx_proxy", 0.0))
        if atrp > 2.5 and adx > 20:
            return "trend"
        if atrp < 1.2 and adx < 18:
            return "meanrevert"
        return "neutral"

class Bandit3:
    def __init__(self):
        self.a = np.ones(3)
        self.b = np.ones(3)
    def choose(self) -> int:
        samples = [np.random.beta(self.a[i], self.b[i]) for i in range(3)]
        return int(np.argmax(samples))
    def update(self, arm: int, reward: float):
        if reward > 0: self.a[arm] += 1
        else: self.b[arm] += 1

class MetaPolicy:
    def __init__(self):
        self.detector = RegimeDetector()
        self.bandit = Bandit3()
    def choose(self, features: Dict) -> Tuple[int, float, str]:
        reg = self.detector.detect(features)
        arm = self.bandit.choose()
        base = [0.33, 0.33, 0.34]
        if reg == "trend": base = [0.5, 0.2, 0.3]
        elif reg == "meanrevert": base = [0.2, 0.5, 0.3]
        labels = ["trend", "mean-revert", "insti"]
        return arm, base[arm], labels[arm]
