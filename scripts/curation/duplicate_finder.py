#!/usr/bin/env python3
"""
Core duplicate finding functionality using FAISS similarity search.
"""

from typing import Dict, List, Optional, Tuple
import numpy as np
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.common.storage.schema import Experience, CategoryManual, Embedding, FAISSMetadata
from src.common.storage.repository import EmbeddingRepository


class DuplicateFinder:
    def __init__(self, db_path: Path, high_threshold: float = 0.92, 
                 medium_threshold: float = 0.75, low_threshold: float = 0.55):
        self.db_path = db_path
        self.high_threshold = high_threshold
        self.medium_threshold = medium_threshold
        self.low_threshold = low_threshold

    def bucket_score(self, score: float) -> str:
        """Classify similarity score into bucket."""
        if score >= self.high_threshold:
            return "high"
        elif score >= self.medium_threshold:
            return "medium"
        elif score >= self.low_threshold:
            return "low"
        else:
            return "none"

    def find_duplicates(
        self,
        compare_pending: bool = False,
        limit: int = 50,
        include_manuals: bool = False,
        bucket_filter: Optional[str] = None  # Added to support bucket-specific searches
    ) -> List[Dict]:
        """Find potential duplicates using FAISS similarity search."""
        # Import FAISS and embedding components
        from src.api.gpu.faiss_manager import FAISSIndexManager
        from src.common.config.config import get_config

        print("Loading FAISS index and embeddings...")

        # Load configuration
        config = get_config()

        # Create database session
        engine = create_engine(f"sqlite:///{self.db_path}")
        Session = sessionmaker(bind=engine)
        session = Session()

        try:
            # Get pending entries to compare
            pending_experiences = session.query(Experience).filter(
                Experience.sync_status == 0  # PENDING
            ).all()

            pending_manuals = []
            if include_manuals:
                pending_manuals = session.query(CategoryManual).filter(
                    CategoryManual.sync_status == 0  # PENDING
                ).all()

            all_pending = pending_experiences + pending_manuals

            if not all_pending:
                print("No pending entries found.")
                return []

            print(f"Found {len(pending_experiences)} pending experiences, {len(pending_manuals)} pending manuals")

            # Load all embeddings to get mapping
            emb_repo = EmbeddingRepository(session)

            sample_emb = None
            for pending_item in all_pending:
                sample_emb = session.query(Embedding).filter(
                    Embedding.entity_id == pending_item.id,
                    Embedding.model_version == config.embedding_model
                ).first()
                if sample_emb:
                    break
            if not sample_emb:
                print("❌ Error: No embeddings found for pending items")
                return []

            # Load FAISS index
            index_dir = self.db_path.parent / "faiss_index"
            if not index_dir.exists():
                print(f"❌ Error: FAISS index not found at {index_dir}")
                print("   Run build_curation_index.py first")
                return []

            faiss_manager = FAISSIndexManager(
                index_dir=str(index_dir),
                dimension=len(emb_repo.to_numpy(sample_emb)),
                model_name=config.embedding_model,
                session=session,
            )

            # Prepare results
            results = []

            # For each pending item, search for similar items
            for pending_item in all_pending:
                print(f"Searching for duplicates for {pending_item.__class__.__name__} {pending_item.id}...")

                # Get embedding for this pending item
                pending_emb = session.query(Embedding).filter(
                    Embedding.entity_id == pending_item.id,
                    Embedding.model_version == config.embedding_model
                ).first()

                if not pending_emb:
                    print(f"  ⚠️  No embedding found for {pending_item.id}, skipping")
                    continue

                # Convert embedding vector
                pending_vector = emb_repo.to_numpy(pending_emb)

                # Search in FAISS
                # If compare_pending is True, only compare to other pending items
                # Otherwise compare to all non-pending items (synced/rejected)
                if compare_pending:
                    # For pending vs pending comparison, search with full index but filter results later
                    distances, indices = faiss_manager.search(pending_vector.reshape(1, -1), limit * 2)  # Search more to have pending items
                else:
                    # Search all and then filter based on status
                    distances, indices = faiss_manager.search(pending_vector.reshape(1, -1), limit * 2)  # Search more to filter later

                # Handle potential shape issues with FAISS results
                # FAISS may return 1D arrays that need to be treated as 2D
                if len(distances.shape) == 1:
                    distances = distances.reshape(1, -1)
                    indices = indices.reshape(1, -1)

                # Get the results and filter based on sync_status if not comparing pending
                added = 0
                for dist, idx in zip(distances[0], indices[0]):
                    if added >= limit:  # Only take top limit results after filtering
                        break

                    # Get the entity_id from the FAISS metadata
                    # Convert numpy.int64 to Python int for SQLAlchemy compatibility
                    faiss_meta = session.query(FAISSMetadata).filter(FAISSMetadata.internal_id == int(idx)).first()
                    if not faiss_meta or faiss_meta.deleted:
                        continue

                    anchor_id = faiss_meta.entity_id
                    anchor_type = faiss_meta.entity_type

                    # Skip if it's the same item
                    if anchor_id == pending_item.id:
                        continue

                    if isinstance(pending_item, Experience) and anchor_type != "experience":
                        continue
                    if isinstance(pending_item, CategoryManual) and anchor_type != "manual":
                        continue

                    anchor_entity = None
                    if anchor_type == "experience":
                        anchor_entity = session.query(Experience).filter(Experience.id == anchor_id).first()
                    elif anchor_type == "manual":
                        if not include_manuals:
                            continue
                        anchor_entity = session.query(CategoryManual).filter(CategoryManual.id == anchor_id).first()

                    if not anchor_entity:
                        continue

                    # Calculate similarity score
                    # IndexFlatIP returns inner product scores (already similarity, not distance)
                    # For normalized vectors, scores are in [0, 1] where higher = more similar
                    similarity_score = float(dist)  # Already a similarity score, no conversion needed

                    # If not comparing pending, filter out pending items
                    if compare_pending:
                        if anchor_entity.sync_status != 0:
                            continue
                    else:
                        if anchor_entity.sync_status == 0:  # PENDING
                            continue

                    # Classify into bucket
                    bucket = self.bucket_score(similarity_score)

                    # Apply bucket filter if specified
                    if bucket_filter and bucket != bucket_filter:
                        continue

                    # Check for section mismatch if both are experiences
                    section_mismatch = False
                    if isinstance(pending_item, Experience) and anchor_type == "experience":
                        anchor_entity = session.query(Experience).filter(Experience.id == anchor_id).first()
                        if anchor_entity and anchor_entity.section != pending_item.section:
                            section_mismatch = True

                    # Create result entry
                    result = {
                        "pending_id": pending_item.id,
                        "pending_type": pending_item.__class__.__name__.lower(),
                        "anchor_id": anchor_id,
                        "anchor_type": anchor_type,
                        "score": similarity_score,
                        "bucket": bucket,
                        "category": pending_item.category_code,
                        "pending_title": pending_item.title[:50] + "..." if len(pending_item.title) > 50 else pending_item.title,
                        "anchor_title": anchor_entity.title[:50] + "..." if anchor_entity and len(anchor_entity.title) > 50 else (anchor_id if anchor_entity is None else anchor_entity.title),
                        "section_mismatch": section_mismatch,
                        "id_collision_flag": pending_item.id == anchor_id,  # This would be rare
                    }

                    results.append(result)
                    added += 1

            print(f"Found {len(results)} potential duplicates")

            return results

        except ImportError as e:
            print(f"❌ Missing dependency: {e}")
            print("Please ensure you have the required packages installed (numpy, sqlalchemy, etc.)")
            return []
        except Exception as e:
            print(f"❌ Error during similarity search: {e}")
            import traceback
            traceback.print_exc()
            return []
        finally:
            session.close()

    def iterative_curation_session(self, compare_pending: bool = False, limit: int = 50,
                                  include_manuals: bool = False) -> Dict[str, int]:
        """
        Execute an iterative curation session following the iterative workflow:
        1. Process all high-similarity items until none remain
        2. Process all medium-similarity items until none remain
        3. Recompute similarities and repeat until no more high/medium found
        4. Return statistics about the curation cycles
        """
        print("Starting iterative curation session...")
        print("Phase 1: Processing high-similarity items (≥0.92)...")

        cycle_count = 0
        total_merges = 0
        total_reviews = 0

        while True:
            cycle_count += 1
            print(f"\n--- Curation Cycle {cycle_count} ---")

            # First, find and count high-similarity items
            high_results = self.find_duplicates(
                compare_pending=compare_pending,
                limit=limit,
                include_manuals=include_manuals,
                bucket_filter="high"
            )

            high_count = len(high_results)
            print(f"Found {high_count} high-similarity items to process")

            if high_count > 0:
                print("Please process high-similarity items using interactive mode.")
                print("After processing, rebuild the index to reflect merged items.")
                break  # For now, break to let user handle the interactive process

            # If no high-similarity items, check medium-similarity items
            medium_results = self.find_duplicates(
                compare_pending=compare_pending,
                limit=limit,
                include_manuals=include_manuals,
                bucket_filter="medium"
            )

            medium_count = len(medium_results)
            print(f"Found {medium_count} medium-similarity items to process")

            if medium_count > 0:
                print("Please process medium-similarity items using interactive mode.")
                print("After processing, rebuild the index to reflect merged items.")
                break  # For now, break to let user handle the interactive process

            # If no high or medium items remain, we've converged
            if high_count == 0 and medium_count == 0:
                print("Curation convergence reached: no more high or medium similarity items found.")
                break

        return {
            "cycles_completed": cycle_count,
            "high_items_processed": high_count,
            "medium_items_processed": medium_count,
            "converged": (high_count == 0 and medium_count == 0)
        }
