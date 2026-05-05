import sqlite3
import struct
from pathlib import Path

import numpy as np
import streamlit as st


DB_PATH = Path(__file__).parent / "crm.sqlite"


@st.cache_resource(show_spinner=False)
def get_connection():
    """Shared Streamlit database connection provider."""
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Database not found: {DB_PATH}")
    return sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, check_same_thread=False)


def blob_to_vector(blob, dim):
    """Unpack a SQLite vector blob into a numpy array."""
    return np.array(struct.unpack(f"{dim}f", blob), dtype=np.float32)


def vector_to_blob(vec):
    """Pack a numpy vector into a SQLite-compatible blob."""
    return struct.pack(f"{len(vec)}f", *vec.astype(np.float32))


def cosine_similarity(v1, v2):
    """Compute cosine similarity while handling zero vectors."""
    norm1 = np.linalg.norm(v1)
    norm2 = np.linalg.norm(v2)
    if norm1 == 0 or norm2 == 0:
        return 0.0
    return np.dot(v1, v2) / (norm1 * norm2)
