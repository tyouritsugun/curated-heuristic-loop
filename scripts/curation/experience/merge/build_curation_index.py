#!/usr/bin/env python3
"""
Build embeddings and FAISS index for curation database.

This script generates embeddings for all pending experiences and skills in the
curation database, then builds a FAISS index for similarity search.

IMPORTANT: The API server must be stopped before running this script to avoid
           conflicts with the GPU/embedding models.

Usage:
    # With default path from scripts_config.yaml:
    python scripts/curation/experience/merge/build_curation_index.py

    # With explicit path:
    python scripts/curation/experience/merge/build_curation_index.py \\
        --db-path data/curation/chl_curation.db
"""

import argparse
import socket
import sys
import time
from pathlib import Path

# Add project root to sys.path
repo_root = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(repo_root))

from scripts._config_loader import load_scripts_config


def check_api_server_running(port=8000):
    """Check if API server is running on the specified port."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(1)
    try:
        result = sock.connect_ex(('localhost', port))
        sock.close()
        return result == 0
    except Exception:
        return False


def parse_args():
    # Load config to get defaults
    try:
        config, _ = load_scripts_config()
        curation_config = config.get("curation", {})
        default_db_path = curation_config.get("curation_db_path", "data/curation/chl_curation.db")
    except Exception:
        # Fallback to hard-coded default if config loading fails
        default_db_path = "data/curation/chl_curation.db"

    parser = argparse.ArgumentParser(
        description="Build embeddings and FAISS index for curation database"
    )
    parser.add_argument(
        "--db-path",
        default=default_db_path,
        help=f"Path to curation database (default: {default_db_path})",
    )
    parser.add_argument(
        "--skip-server-check",
        action="store_true",
        help="Skip API server check (use with caution)",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    db_path = Path(args.db_path)

    # Validate database exists
    if not db_path.exists():
        print(f"❌ Error: Database does not exist: {db_path}", file=sys.stderr)
        print("   Run init_curation_db.py and import_to_curation_db.py first")
        sys.exit(1)

    # Check if API server is running
    if not args.skip_server_check:
        print("Checking if API server is running...")
        if check_api_server_running(8000):
            print()
            print("❌ Error: API server is running on port 8000")
            print()
            print("The API server must be stopped before building the curation index")
            print("to avoid GPU/model conflicts.")
            print()
            print("Please stop the API server and try again:")
            print("  1. Press Ctrl+C in the terminal running the API server")
            print("  2. Or run: pkill -f 'python -m src.api.server'")
            print()
            print("If you're sure the API is not using the models, use --skip-server-check")
            sys.exit(1)
        print("✓ API server is not running")
        print()

    # Sleep 3 seconds before starting
    print("Waiting 3 seconds before starting...")
    time.sleep(3)
    print()

    # Import dependencies (after server check)
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from src.common.storage.schema import Experience, CategorySkill
    from src.common.storage.repository import EmbeddingRepository
    from src.common.config.config import get_config

    print(f"Building embeddings and index for: {db_path}")
    print()

    # Create database session
    engine = create_engine(f"sqlite:///{db_path}")
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        # Check backend
        config = get_config()
        if config.backend == "cpu":
            print("❌ Error: CPU backend detected")
            print("   Curation workflow requires GPU backend for embeddings")
            print("   Carlos must run in GPU mode")
            sys.exit(1)

        print(f"Backend: {config.backend}")
        print(f"Embedding model: {config.embedding_model}")
        print()

        # Load embedding model
        print("Loading embedding model...")
        from src.api.gpu.embedding_client import EmbeddingClient

        # Parse model name (format: "repo:quant")
        model_parts = config.embedding_model.split(":")
        model_repo = model_parts[0]
        quantization = model_parts[1] if len(model_parts) > 1 else "fp32"

        embedding_client = EmbeddingClient(
            model_repo=model_repo,
            quantization=quantization,
            n_gpu_layers=config.embedding_n_gpu_layers,
        )
        print("✓ Model loaded")
        print()

        # Get pending experiences and skills
        pending_experiences = session.query(Experience).filter(
            Experience.embedding_status == "pending"
        ).all()

        pending_skills = session.query(CategorySkill).filter(
            CategorySkill.embedding_status == "pending"
        ).all()

        total_pending = len(pending_experiences) + len(pending_skills)

        if total_pending > 0:
            print(f"Found {len(pending_experiences)} pending experiences")
            print(f"Found {len(pending_skills)} pending skills")
            print(f"Total: {total_pending} entries to process")
            print()
        else:
            print("No pending entries found. All embeddings are up to date.")
            print("Proceeding to build/rebuild FAISS index...")
            print()

        emb_repo = EmbeddingRepository(session)

        # Process experiences
        if pending_experiences:
            print(f"Processing experiences: 0/{len(pending_experiences)}", end="", flush=True)
            for i, exp in enumerate(pending_experiences, 1):
                content = f"{exp.title}\n\n{exp.playbook}"

                try:
                    # Mark as processing
                    exp.embedding_status = "processing"
                    session.flush()

                    # Generate embedding
                    embedding_vector = embedding_client.encode_single(content)

                    # Delete any existing embedding to avoid duplicates on re-embed
                    emb_repo.delete_by_entity(exp.id, "experience")

                    # Save new embedding
                    emb_repo.create(
                        entity_id=exp.id,
                        entity_type="experience",
                        category_code=exp.category_code,
                        vector=embedding_vector,
                        model_version=config.embedding_model,
                    )

                    # Update status to embedded (not "synced")
                    exp.embedding_status = "embedded"

                except Exception as e:
                    print(f"\n❌ Error processing experience {exp.id}: {e}")
                    exp.embedding_status = "failed"

                # Progress update
                print(f"\rProcessing experiences: {i}/{len(pending_experiences)}", end="", flush=True)

            session.commit()
            print()  # New line after progress
            print(f"✓ Embeddings generated for {len(pending_experiences)} experiences")
            print()

        # Process skills
        if pending_skills:
            print(f"Processing skills: 0/{len(pending_skills)}", end="", flush=True)
            for i, skill in enumerate(pending_skills, 1):
                content = f"{skill.title}\n\n{skill.content}"

                try:
                    # Mark as processing
                    skill.embedding_status = "processing"
                    session.flush()

                    # Generate embedding
                    embedding_vector = embedding_client.encode_single(content)

                    # Delete any existing embedding to avoid duplicates on re-embed
                    emb_repo.delete_by_entity(skill.id, "skill")

                    # Save new embedding
                    emb_repo.create(
                        entity_id=skill.id,
                        entity_type="skill",
                        category_code=skill.category_code,
                        vector=embedding_vector,
                        model_version=config.embedding_model,
                    )

                    # Update status to embedded (not "synced")
                    skill.embedding_status = "embedded"

                except Exception as e:
                    print(f"\n❌ Error processing skill {skill.id}: {e}")
                    skill.embedding_status = "failed"

                # Progress update
                print(f"\rProcessing skills: {i}/{len(pending_skills)}", end="", flush=True)

            session.commit()
            print()  # New line after progress
            print(f"✓ Embeddings generated for {len(pending_skills)} skills")
            print()

        # Build FAISS index
        print("Building FAISS index...")
        from src.api.gpu.faiss_manager import FAISSIndexManager
        from src.common.storage.schema import FAISSMetadata
        import numpy as np
        import shutil

        # Determine index path
        index_dir = db_path.parent / "faiss_index"

        # Clear existing index files and metadata to avoid duplication
        if index_dir.exists():
            print("  Removing existing index files...")
            shutil.rmtree(index_dir)

        index_dir.mkdir(parents=True, exist_ok=True)

        # Clear FAISS metadata table
        print("  Clearing faiss_metadata table...")
        session.query(FAISSMetadata).delete()
        session.commit()

        # Initialize FAISS manager with fresh index
        dimension = len(embedding_client.encode_single("test"))
        faiss_manager = FAISSIndexManager(
            index_dir=str(index_dir),
            dimension=dimension,
            model_name=config.embedding_model,
            session=session,
        )

        # Get all embeddings
        all_embeddings = emb_repo.get_all_by_model(config.embedding_model, entity_type=None)

        if not all_embeddings:
            print("⚠️  No embeddings found, skipping FAISS index build")
            session.close()
            return

        entity_ids = []
        entity_types = []
        embedding_vectors = []

        for emb in all_embeddings:
            entity_ids.append(emb.entity_id)
            entity_types.append(emb.entity_type)
            embedding_vectors.append(emb_repo.to_numpy(emb))

        embedding_array = np.vstack(embedding_vectors).astype(np.float32)

        # Add to FAISS index (order: entity_ids, entity_types, embeddings)
        faiss_manager.add(entity_ids, entity_types, embedding_array)
        faiss_manager.save()

        print(f"✓ FAISS index built ({len(all_embeddings)} vectors, {dimension} dimensions)")
        print(f"✓ Index saved to: {index_dir}")
        print()

        print("✅ Build complete!")
        print()
        print("Next steps:")
        print("  1. Run duplicate detection")
        print("  2. Or export approved data for publishing")

        # Commit changes in success case
        session.commit()

    except Exception as e:
        print(f"\n❌ Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        session.rollback()
        sys.exit(1)
    finally:
        session.close()


if __name__ == "__main__":
    main()
