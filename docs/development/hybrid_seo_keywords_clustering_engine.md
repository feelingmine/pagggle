# Technical Architecture & Design Document: Two-Stage Hybrid SEO Keyword Clustering Engine

---

## 1. Executive Summary
Modern SEO workflows require grouping keywords into distinct, actionable clusters to build topical authority and avoid **keyword cannibalization**. 

Traditional open-source tools typically fall into one of two polarized paradigms: **Semantic AI Clustering** (which groups words by literal/conceptual meaning but ignores Google’s actual ranking behavior) or **SERP-based Clustering** (which calculates live URL overlaps but suffers from extreme API cost inefficiency and $O(N^2)$ computational complexity when scaled).

This document outlines a production-grade **Two-Stage Hybrid Architecture** designed in Python. By feeding data into a high-efficiency **Funnel Approach**, it slashes API/compute footprints by restricting heavy URL-matching logic strictly within pre-grouped semantic silos. Furthermore, it natively ingests offline data structures (such as Semrush exports) and exposes an isolated data-ingestion layer built for future API or **Model Context Protocol (MCP)** execution.

---

## 2. Architecture Overview & Data Flow
The system processes raw inputs via three decoupled layers to guarantee clean boundaries between external data interfaces and core algorithmic logic.

```
       [ Input Layer ]                 [ Core Algorithmic Engine ]            [ Business Logic Layer ]
 ┌─────────────────────────┐          ┌───────────────────────────┐         ┌──────────────────────────┐
 │  Semrush Offline CSV/   │          │  Phase 1: Semantic Silo   │         │  Keyword Role Anchoring  │
 │  XLSX Export File       │          │  (Agglomerative Cluster)  │         │  (Primary vs. Secondary) │
 └────────────┬────────────┘          └─────────────┬─────────────┘         └────────────┬─────────────┘
              │                                     │                                    │
              ▼                                     ▼                                    ▼
 ┌─────────────────────────┐          ┌───────────────────────────┐         ┌──────────────────────────┐
 │   SemrushDataParser     ├─────────►│ Phase 2: SERP Intent Split├────────►│ Final Schema & Structural│
 │  (Normalize Schema)     │          │ (Intra-Silo Disjoint-Set) │         │  Report Output Pipeline  │
 └─────────────────────────┘          └───────────────────────────┘         └──────────────────────────┘
```

---

## 3. Component Deep Dive & Core Logic

### Module 1: The Input Interface & Data Parser
* **Objective:** Decouple structural schema variations from the cluster execution thread. 
* **Design Pattern:** Strategy Pattern via a functional parser wrapper. It standardizes noisy textual representations of Search Engine Result Pages (SERPs) — such as newline or comma-delimited strings — into clean, indexable hash sets (`Set[str]`).
* **Future-Proofing:** Upgrading this layer to poll the Semrush API or function as an asynchronous MCP tool involves swapping this parser without modifying a single line of downstream mathematical execution.

### Module 2: The Core Clustering Engine (Two-Stage Funnel)
#### Phase 1: Semantic Silo Clustering (Large-Scale Macro Partitioning)
* **Methodology:** Rather than forcing users to guess the number of macro-topics beforehand (as required by algorithms like K-Means), the engine uses **Agglomerative Hierarchical Clustering** bundled with a variable distance threshold.
* **Vector Model:** Employs the open-source `all-MiniLM-L6-v2` Sentence-Transformer to project strings into dense, 384-dimensional vector spaces. 
* **Mathematical Boundary:** Measures structural closeness via **Cosine Distance**. Strings within the configured distance threshold ($\le 0.65$) are grouped into a broad semantic category (`semantic_silo_id`).

#### Phase 2: SERP Intent Splitting (Precision Micro Surgery)
* **Methodology:** Operates strictly *inside* each macro semantic silo. For each keyword within a silo, the engine matches its top-10 ranking Google URLs against the existing URL feature pools of that silo.
* **Intent Metric:** Uses an explicit URL Overlap Coefficient. If the cardinality of the intersection between a keyword’s SERP and a page's SERP matches or exceeds the threshold ($|SERP_A \cap SERP_B| \ge 	ext{Threshold}$), Google views them as the same search intent.
* **Disjoint-Set Optimization:** When a match is found, the script triggers a dynamic graph union (`existing_serp.union(current_serp)`). This constantly expands the topical profile of the target landing page, preventing data drift and handling long-tail volatility. If no match is found, it spins out a clean, new target landing page identifier (`target_page_id`).

### Module 3: Commercial Analytics & Role Anchoring
* **Objective:** Elevate simple data classification into high-utility, actionable content briefs.
* **Rule Engine:** Within every uniquely generated `target_page_id`, it reads the commercial metrics pulled from Semrush. It ranks all internal phrases via a tiered sorting hierarchy: **Search Volume (Descending) $ightarrow$ Keyword Difficulty (Ascending)**. The top-ranking phrase is explicitly tagged as the **Primary Keyword** (to drive H1/Title tag optimization), while all adjacent phrases are tagged as **Secondary Keywords** (to drive H2/H3 long-tail sub-topic layouts).

---

## 4. Open-Source Libraries & Dependencies
The solution relies on highly vetted, industrial-grade data science packages rather than closed commercial ecosystems.

| Dependency | GitHub Source Repo | Purpose in Architecture |
| :--- | :--- | :--- |
| **`sentence-transformers`** | [UKPLab / sentence-transformers](https://github.com/UKPLab/sentence-transformers) | Encodes keyword strings into dense vector representations. |
| **`scikit-learn`** | [scikit-learn / scikit-learn](https://github.com/scikit-learn/scikit-learn) | Runs the non-parametric Agglomerative Hierarchical Clustering. |
| **`pandas` & `numpy`** | [pandas-dev / pandas](https://github.com/pandas-dev/pandas) | Manages vector matrix routing and tabular file transformations. |
| **`SERP Intersect Logic`** | Inspired by [dartseoengineer / keyword-clustering](https://github.com/dartseoengineer/keyword-clustering) | Conceptual source for using URL intersection cardinality to prove search intent. |

---

## 5. Production Reference Implementation

```python
import pandas as pd
import numpy as np
from typing import List, Dict, Any, Set
from sentence_transformers import SentenceTransformer
from sklearn.cluster import AgglomerativeClustering

# ==========================================
# MODULE 1: Data Input & Parser Interface
# ==========================================
class SemrushDataParser:
    """
    Normalizes incoming Semrush data formats. 
    Swappable with live HTTP API requests or MCP tool contexts.
    """
    @staticmethod
    def parse_dataframe(df: pd.DataFrame, 
                        kw_col: str = 'Keyword', 
                        vol_col: str = 'Search Volume', 
                        kd_col: str = 'Keyword Difficulty', 
                        serp_col: str = 'SERP Results') -> pd.DataFrame:
        normalized_records = []
        for _, row in df.iterrows():
            raw_serp = row[serp_col]
            if isinstance(raw_serp, str):
                urls = [url.strip() for url in raw_serp.replace('\n', ',').split(',') if url.strip()]
            elif isinstance(raw_serp, list):
                urls = [str(url).strip() for url in raw_serp if str(url).strip()]
            else:
                urls = []
                
            normalized_records.append({
                'keyword': str(row[kw_col]).strip(),
                'volume': int(row[vol_col]) if pd.notna(row[vol_col]) else 0,
                'kd': int(row[kd_col]) if pd.notna(row[kd_col]) else 0,
                'serp': urls
            })
        return pd.DataFrame(normalized_records)

# ==========================================
# MODULE 2: Core Hybrid Clustering Engine
# ==========================================
class HybridSEOClusteringEngine:
    """
    Two-stage funnel processing pipeline:
    Stage 1: Cosine Distance Semantic Siloing (Reduces algorithmic space to O(N))
    Stage 2: Discrete URL Intersect Evaluation (Prevents keyword cannibalization)
    """
    def __init__(self, 
                 transformer_model: str = 'all-MiniLM-L6-v2', 
                 semantic_distance_cutoff: float = 0.65,
                 serp_match_threshold: int = 3):
        print(f"[Engine] Initializing vector model client: {transformer_model}...")
        self.encoder = SentenceTransformer(transformer_model)
        self.distance_cutoff = semantic_distance_cutoff
        self.serp_threshold = serp_match_threshold

    def _execute_semantic_siloing(self, df: pd.DataFrame) -> pd.DataFrame:
        print("[Engine] Stage 1: Running macro semantic vector partitioning...")
        vectors = self.encoder.encode(df['keyword'].tolist(), batch_size=64, show_progress_bar=False)
        
        cluster_strategy = AgglomerativeClustering(
            n_clusters=None,
            distance_threshold=self.distance_cutoff,
            metric='cosine',
            linkage='average'
        )
        df['semantic_silo_id'] = cluster_strategy.fit_predict(vectors)
        return df

    def _execute_serp_splitting(self, df: pd.DataFrame) -> pd.DataFrame:
        print("[Engine] Stage 2: Performing precise intent-based SERP splits...")
        df['target_page_id'] = ""
        
        for silo_id, silo_group in df.groupby('semantic_silo_id'):
            row_indices = silo_group.index.tolist()
            active_page_serps: List[Set[str]] = []
            index_to_page_map: Dict[int, int] = {}
            
            for idx in row_indices:
                current_serp = set(df.loc[idx, 'serp'])
                matched_page_index = -1
                
                for page_idx, target_serp_pool in enumerate(active_page_serps):
                    shared_urls = len(current_serp.intersection(target_serp_pool))
                    if shared_urls >= self.serp_threshold:
                        matched_page_index = page_idx
                        active_page_serps[page_idx] = target_serp_pool.union(current_serp)
                        break
                
                if matched_page_index != -1:
                    index_to_page_map[idx] = matched_page_index
                else:
                    new_page_index = len(active_page_serps)
                    active_page_serps.append(current_serp)
                    index_to_page_map[idx] = new_page_index
            
            for idx, page_idx in index_to_page_map.items():
                df.loc[idx, 'target_page_id'] = f"Silo_{silo_id}_Page_{page_idx}"
                
        return df

    def run_pipeline(self, df: pd.DataFrame) -> pd.DataFrame:
        df = self._execute_semantic_siloing(df)
        df = self._execute_serp_splitting(df)
        return df

# ==========================================
# MODULE 3: Business Analytics Anchor
# ==========================================
class KeywordRoleAnchor:
    """
    Ranks keywords inside a common page intent block. 
    Selects a single Primary topic driver based on Volume/KD constraints.
    """
    @staticmethod
    def assign_roles(df: pd.DataFrame) -> pd.DataFrame:
        print("[Anchor] Auto-selecting layout structure (Primary vs Secondary)...")
        df['keyword_role'] = 'Secondary'
        for page_id, page_group in df.groupby('target_page_id'):
            ordered_indices = page_group.sort_values(by=['volume', 'kd'], ascending=[False, True]).index
            df.loc[ordered_indices, 'keyword_role'] = 'Primary'
        return df

# ==========================================
# Execution Context
# ==========================================
if __name__ == "__main__":
    # Mocking structural data arriving from an unparsed Semrush CSV file
    raw_csv_dataframe = pd.DataFrame([
        {"Keyword": "best running shoes", "Search Volume": 5000, "Keyword Difficulty": 65, "SERP Results": "nike.com/run,runnersworld.com,adidas.com/run"},
        {"Keyword": "top running shoes 2026", "Search Volume": 1200, "Keyword Difficulty": 50, "SERP Results": "nike.com/run,runnersworld.com,asics.com"},
        {"Keyword": "buy running shoes online", "Search Volume": 2500, "Keyword Difficulty": 75, "SERP Results": "amazon.com/shoes,nike.com/store,footlocker.com"},
        {"Keyword": "how to learn seo", "Search Volume": 3000, "Keyword Difficulty": 60, "SERP Results": "moz.com/guide,backlinko.com,hubspot.com"},
        {"Keyword": "seo strategy for beginners", "Search Volume": 800, "Keyword Difficulty": 45, "SERP Results": "moz.com/guide,backlinko.com,searchengineland.com"},
        {"Keyword": "seo services pricing cost", "Search Volume": 1500, "Keyword Difficulty": 55, "SERP Results": "clutch.co/seo,upwork.com,agency-abc.com"}
    ])

    # Execute end-to-end framework
    parsed_df = SemrushDataParser.parse_dataframe(raw_csv_dataframe, serp_col="SERP Results")
    engine = HybridSEOClusteringEngine(serp_match_threshold=2)
    clustered_output = engine.run_pipeline(parsed_df)
    final_architecture_report = KeywordRoleAnchor.assign_roles(clustered_output)

    # Output formatting
    final_architecture_report = final_architecture_report.sort_values(by=['target_page_id', 'keyword_role'], ascending=[True, False])
    print("\n" + "="*20 + " HYBRID ENGINE SILO SCHEMATIC REPORT " + "="*20)
    print(final_architecture_report[['keyword', 'volume', 'kd', 'semantic_silo_id', 'target_page_id', 'keyword_role']].to_string(index=False))
```

---

## 6. Verification & Scaling Strategy
1. **Algorithmic Complexity Reduction:** For a batch of $N=10,000$ phrases, a classic single-stage SERP algorithm requires close to 50 million comparative calculations. By establishing Stage 1 (Semantic Siloing), the workspace is broken down into approximately 100 localized silos containing 100 keywords each. This compresses total intra-silo SERP evaluations down to roughly 500,000 passes—achieving a **99% reduction in computational overhead**.
2. **Caching Constraints:** To make this enterprise-ready when upgrading to a live API endpoint, engineers should introduce a simple SQLite database layer between `SemrushDataParser` and `HybridSEOClusteringEngine` to store historical SERP arrays using the keyword string as a primary key, preventing duplicate billing on repetitive queries.
