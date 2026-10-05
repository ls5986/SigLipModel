"""Legacy V1 classifiers; retain class paths/layout for existing joblib artifacts.

New physical-evidence components live in text_model/structured_model/fusion_model.
V1 metadata intentionally retains its historical text+structured feature layout.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass

from acquisition_metadata import _date, acquisition_time_metadata

import numpy as np
from scipy.sparse import csr_matrix, hstack
from sklearn.feature_extraction import DictVectorizer
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score, balanced_accuracy_score, brier_score_loss,
    confusion_matrix, f1_score, ndcg_score, precision_score, recall_score,
    roc_auc_score,
)

FEATURE_POLICY = {
    "version":"metadata-feature-policy-v1",
    "fields":{
        "YearBuilt":{"type":"numeric","available":"listing time","leakage":"none"},
        "PhotosCount":{"type":"numeric","available":"listing time","leakage":"none"},
        "LivingArea":{"type":"numeric","available":"listing time","leakage":"none"},
        "BedroomsTotal":{"type":"numeric","available":"listing time","leakage":"none"},
        "BathroomsTotalInteger":{"type":"numeric","available":"listing time","leakage":"none"},
        "ListPrice":{"type":"numeric","available":"listing time","leakage":"asking price only"},
        "OriginalListPrice":{"type":"numeric","available":"listing time","leakage":"asking price only"},
        "DaysOnMarket":{"type":"numeric","available":"scoring snapshot","leakage":"snapshot pinned"},
        "PriorSaleCount":{"type":"numeric","available":"before listing snapshot","leakage":"future sales excluded"},
        "MonthsSinceMostRecentPriorSale":{"type":"numeric","available":"before listing snapshot","leakage":"future sales excluded"},
        "MostRecentPriorSalePrice":{"type":"numeric","available":"before listing snapshot","leakage":"future sales excluded"},
        "PropertyType":{"type":"category","available":"listing time","leakage":"none"},
        "PropertySubType":{"type":"category","available":"listing time","leakage":"none"},
        "PostalCode":{"type":"category","available":"listing time","leakage":"coarse geography"},
        "City":{"type":"category","available":"listing time","leakage":"coarse geography"},
        "PublicRemarks":{"type":"tfidf","available":"listing time","leakage":"frozen text only"},
    },
    "derived":{
        "price_reduction_pct":"max(0,(OriginalListPrice-ListPrice)/OriginalListPrice)",
    },
    "excluded":[
        "ClosePrice","CloseDate","sales on/after listing snapshot","later sale outcomes","cohort/target identifiers",
        "ListingKey","ListingId","private/contact fields","URLs",
    ],
}

NUMERIC = (
    "YearBuilt","PhotosCount","LivingArea","BedroomsTotal",
    "BathroomsTotalInteger","ListPrice","OriginalListPrice","DaysOnMarket",
    "PriorSaleCount","MonthsSinceMostRecentPriorSale","MostRecentPriorSalePrice",
)
CATEGORICAL = ("PropertyType","PropertySubType","PostalCode","City")


def metadata_row(metadata):
    metadata = metadata or {}
    row = {}
    for name in NUMERIC:
        value = metadata.get(name)
        row[name+"_missing"] = float(value in {None,""})
        if value not in {None,""}:
            try:
                value = float(value)
            except (TypeError,ValueError):
                continue
            if math.isfinite(value):
                row[name] = value
                row[name+"_missing"] = 0.0
    for name in CATEGORICAL:
        value = metadata.get(name)
        row[name+"_missing"] = float(value in {None,""})
        if value not in {None,""}:
            row[name] = str(value).strip().casefold()[:100]
            row[name+"_missing"] = 0.0
    original,row_price = row.get("OriginalListPrice"),row.get("ListPrice")
    if original and row_price is not None and original>0:
        row["price_reduction_pct"] = max(0.0,(original-row_price)/original)
    return row


@dataclass
class MetadataClassifier:
    vectorizer: DictVectorizer
    text: TfidfVectorizer
    model: LogisticRegression

    @classmethod
    def fit(cls,metadata,remarks,y,weights=None):
        vectorizer = DictVectorizer(sparse=True)
        structured = vectorizer.fit_transform([metadata_row(row) for row in metadata])
        text = TfidfVectorizer(
            max_features=2000,ngram_range=(1,2),min_df=1,strip_accents="unicode",
        )
        language = text.fit_transform([value or "__missing__" for value in remarks])
        model = LogisticRegression(
            C=1,max_iter=2000,class_weight="balanced",random_state=20260922,
        )
        model.fit(hstack([structured,language]),y,sample_weight=weights)
        return cls(vectorizer,text,model)

    def matrix(self,metadata,remarks):
        return hstack([
            self.vectorizer.transform([metadata_row(row) for row in metadata]),
            self.text.transform([value or "__missing__" for value in remarks]),
        ])

    def predict(self,metadata,remarks):
        return self.model.predict_proba(self.matrix(metadata,remarks))[
            :,list(self.model.classes_).index(1)
        ]

    def explain(self,metadata,remarks,limit=8):
        matrix = self.matrix([metadata],[remarks])
        names = list(self.vectorizer.get_feature_names_out()) + [
            "remarks:"+name for name in self.text.get_feature_names_out()
        ]
        values = matrix.toarray()[0]*self.model.coef_[0]
        order = np.argsort(np.abs(values))[::-1]
        return [{"feature":names[i],"contribution":float(values[i])}
                for i in order[:limit] if values[i]!=0]


def aggregate_images(vectors,mode):
    values = np.asarray(vectors,dtype=float)
    if values.ndim!=2 or not len(values):
        return None
    if mode=="mean": return values.mean(axis=0)
    if mode=="max": return values.max(axis=0)
    if mode=="mean_max": return np.concatenate([values.mean(axis=0),values.max(axis=0)])
    raise ValueError("Unsupported image aggregation")


@dataclass
class VisionClassifier:
    aggregation: str
    model: LogisticRegression

    @classmethod
    def fit(cls,bags,y,aggregation="mean_max",weights=None):
        values = [aggregate_images(bag,aggregation) for bag in bags]
        if any(value is None for value in values):
            raise ValueError("Vision training requires usable image bags")
        model = LogisticRegression(
            C=.25,max_iter=2000,class_weight="balanced",random_state=20260922,
        )
        model.fit(np.stack(values),y,sample_weight=weights)
        return cls(aggregation,model)

    def predict(self,bags):
        values = [aggregate_images(bag,self.aggregation) for bag in bags]
        result = np.full(len(values),np.nan)
        indices = [i for i,value in enumerate(values) if value is not None]
        if indices:
            scores = self.model.predict_proba(np.stack([values[i] for i in indices]))[
                :,list(self.model.classes_).index(1)
            ]
            result[indices] = scores
        return result

    def image_influence(self,vectors):
        return self.predict([[vector] for vector in vectors])


@dataclass
class FusionClassifier:
    model: LogisticRegression

    @staticmethod
    def rows(metadata_scores,vision_scores,image_counts):
        rows = []
        for meta,vision,count in zip(metadata_scores,vision_scores,image_counts):
            meta_available = float(np.isfinite(meta))
            vision_available = float(np.isfinite(vision))
            rows.append([
                float(meta) if meta_available else .5,
                float(vision) if vision_available else .5,
                meta_available,vision_available,math.log1p(count),
            ])
        return np.asarray(rows)

    @classmethod
    def fit(cls,metadata_scores,vision_scores,image_counts,y,weights=None):
        model = LogisticRegression(
            C=1,max_iter=1500,class_weight="balanced",random_state=20260922,
        )
        model.fit(cls.rows(metadata_scores,vision_scores,image_counts),y,
                  sample_weight=weights)
        return cls(model)

    def predict(self,metadata_scores,vision_scores,image_counts):
        return self.model.predict_proba(self.rows(
            metadata_scores,vision_scores,image_counts
        ))[:,list(self.model.classes_).index(1)]


def classification_metrics(y,scores,k_values=(20,50)):
    y = np.asarray(y,dtype=int)
    scores = np.asarray(scores,dtype=float)
    finite = np.isfinite(scores)
    y,scores = y[finite],scores[finite]
    if not len(y):
        return {"n":0}
    prediction = scores>=.5
    result = {
        "n":len(y),
        "balanced_accuracy":float(balanced_accuracy_score(y,prediction)),
        "precision":float(precision_score(y,prediction,zero_division=0)),
        "recall":float(recall_score(y,prediction,zero_division=0)),
        "f1":float(f1_score(y,prediction,zero_division=0)),
        "confusion_matrix_no_yes":confusion_matrix(y,prediction,labels=[0,1]).tolist(),
        "roc_auc":float(roc_auc_score(y,scores)) if len(set(y))==2 else None,
        "pr_auc":float(average_precision_score(y,scores)) if len(set(y))==2 else None,
        "brier":float(brier_score_loss(y,scores)),
        "ndcg":float(ndcg_score([y],[scores])) if y.sum() else None,
    }
    order = np.argsort(scores)[::-1]
    positives = max(1,int(y.sum()))
    for k in k_values:
        size = min(k,len(y))
        selected = y[order[:size]]
        result[f"precision_at_{k}"] = float(selected.mean()) if size else None
        result[f"recall_at_{k}"] = float(selected.sum()/positives) if size else None
    return result


def require_class_diversity(labels,groups,minimum_per_class=5,minimum_groups=3):
    counts = Counter(int(value) for value in labels)
    if min(counts.get(0,0),counts.get(1,0))<minimum_per_class:
        raise ValueError("Need at least five explicit TARGET and NOT_TARGET examples")
    by_class = {
        label:len({group for group,value in zip(groups,labels) if int(value)==label})
        for label in (0,1)
    }
    if min(by_class.values())<minimum_groups:
        raise ValueError("Need at least three independent groups in each class")
    return {"labels":dict(counts),"groups":by_class}
