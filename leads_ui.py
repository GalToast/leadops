"""
LeadOps UI - Query your lead database
Run: streamlit run leads_ui.py
"""

import streamlit as st
import sqlite3
import pandas as pd
import numpy as np
import re
import struct
import json
import html
from pathlib import Path
from core_utils import DB_PATH, get_connection, blob_to_vector, vector_to_blob, cosine_similarity

def escape_markdown(text):
    """Escape special characters to prevent XSS in markdown rendering."""
    if text is None:
        return ""
    return html.escape(str(text))

# Page config is now in app.py for multipage support

if not DB_PATH.exists():
    st.error(f"⚠️ Database not found at `{DB_PATH}`")
    st.info("Please ensure `crm.sqlite` is in the same directory as this script.")
    st.stop()

def validate_schema():
    """Ensure all required tables and views exist."""
    required = {
        'tables': ['leadops_leads', 'leadops_profiles', 'leadops_vector_embeddings', 'leadops_search_fts'],
        'views': ['leadops_v_audience_classification', 'leadops_v_send_now', 'leadops_v_needs_research']
    }
    
    conn = get_connection()
    missing = []
    
    for table in required['tables']:
        res = pd.read_sql_query("SELECT name FROM sqlite_master WHERE type='table' AND name=?", conn, params=[table])
        if res.empty: missing.append(f"Table: {table}")
        
    for view in required['views']:
        res = pd.read_sql_query("SELECT name FROM sqlite_master WHERE type='view' AND name=?", conn, params=[view])
        if res.empty: missing.append(f"View: {view}")
        
    if missing:
        st.error("❌ Database Schema Incomplete")
        st.write("The following required components are missing:")
        for m in missing:
            st.write(f"- {m}")
        st.info("Please run your database migrations or setup scripts.")
        st.stop()

validate_schema()
conn = get_connection()

# ─────────────────────────────────────────────────────────────────────────────
# Vector Similarity Functions
# ─────────────────────────────────────────────────────────────────────────────

def get_embedding_for_lead(lead_id, doc_type='profile_markdown'):
    """Get embedding vector for a specific lead."""
    result = pd.read_sql_query(
        "SELECT vector_blob, embedding_dim FROM leadops_vector_embeddings WHERE lead_id = ? AND doc_type = ?",
        conn, params=[int(lead_id), doc_type]
    )
    if result.empty:
        return None
    return blob_to_vector(result.iloc[0]['vector_blob'], result.iloc[0]['embedding_dim'])

@st.cache_data(ttl=300)
def enrich_leads(lead_ids):
    """Add metadata: embeddings, evidence, profile status."""
    if not lead_ids:
        return pd.DataFrame()
    
    # Use parameterized IN clause for security and performance
    placeholders = ",".join(["?"] * len(lead_ids))
    query = f"""
        SELECT 
            l.lead_id,
            l.name,
            l.status,
            l.outreach_status,
            l.email,
            l.website,
            l.website_domain,
            l.batch,
            (SELECT COUNT(*) FROM leadops_vector_embeddings e WHERE e.lead_id = l.lead_id) as embeddings,
            (SELECT COUNT(*) FROM leadops_evidence_artifacts a WHERE a.lead_id = l.lead_id) as evidence,
            (SELECT COUNT(*) FROM leadops_drafts d WHERE d.lead_id = l.lead_id) as drafts,
            (SELECT COUNT(*) FROM leadops_outreach_events o WHERE o.lead_id = l.lead_id) as outreach_events,
            CASE WHEN p.lead_id IS NOT NULL THEN 1 ELSE 0 END as has_profile,
            p.title,
            p.outreach_angle,
            ac.audience_family,
            ac.audience_type
        FROM leadops_leads l
        LEFT JOIN leadops_profiles p ON l.lead_id = p.lead_id
        LEFT JOIN leadops_v_audience_classification ac ON l.lead_id = ac.lead_id
        WHERE l.lead_id IN ({placeholders})
    """
    
    return pd.read_sql_query(query, conn, params=list(map(int, lead_ids)))

def render_lead_card(row, snippet=None, similarity=None):
    """Render a single lead as a rich card."""
    # Status styling mapping
    status_map = {
        "ready": {"icon": "🎯", "label": "Ready", "color": "green"},
        "complete": {"icon": "✅", "label": "Complete", "color": "blue"},
        "disqualified": {"icon": "🚫", "label": "Disqualified", "color": "red"},
        "draft-prepared": {"icon": "✍️", "label": "Drafted", "color": "orange"},
        "new": {"icon": "✨", "label": "New", "color": "grey"}
    }
    # Safely handle None/missing status values
    status_val = row['status'] or 'unknown'
    st_info = status_map.get(status_val.lower(), {"icon": "⚪", "label": status_val, "color": "grey"})
    
    outreach_map = {
        "sent": "📨 Sent",
        "replied": "🎉 Replied",
        "bounced": "💥 Bounced",
        "drafted": "📝 Drafted",
        "uncontacted": "⏳ New"
    }
    outreach_val = row['outreach_status'] or 'unknown'
    out_label = outreach_map.get(outreach_val.lower(), outreach_val)
    
    with st.container(border=True):
        # Anchor for scroll preservation
        st.markdown(f'<div id="lead-{row["lead_id"]}"></div>', unsafe_allow_html=True)
        
        # Header: Name and badges
        h1, h2 = st.columns([4, 1])
        with h1:
            st.markdown(f"#### {st_info['icon']} [{row['name']}](?lead_id={row['lead_id']}#lead-{row['lead_id']})")
        with h2:
            if similarity:
                st.markdown(f"**{similarity*100:.1f}% Match**")
            else:
                st.markdown(f":{st_info['color']}[**{st_info['label']}**]")

        # Sub-header: Metadata pills
        meta_cols = st.columns([1, 1, 1, 2])
        meta_cols[0].caption(out_label)
        if row['batch']:
            meta_cols[1].caption(f"📦 Batch {row['batch']}")
        if row['audience_family']:
            meta_cols[2].caption(f"👥 {row['audience_family']}")
        
        # Main Info
        st.markdown("---")
        c1, c2 = st.columns(2)
        with c1:
            if row['email']:
                st.markdown(f"📧 `{row['email']}`")
            if row['website']:
                domain = row['website_domain'] or row['website']
                st.markdown(f"🌐 [{domain}](https://{row['website']})")
        
        with c2:
            if row['title']:
                title_clean = row['title'].split('\n')[0][:80]
                st.markdown(f"🏷️ *{title_clean}*")
            
        # Optional snippet or angle
        if snippet:
            st.caption(f"🔍 {snippet}")
        elif row['outreach_angle']:
            angle = row['outreach_angle'][:150] + "..." if len(row['outreach_angle']) > 150 else row['outreach_angle']
            with st.expander("💡 Outreach Strategy", expanded=False):
                st.write(angle)
        
        # Footer: Quick Actions
        st.markdown("---")
        a1, a2, a3, a4 = st.columns(4)
        a1.link_button("📄 Details", f"?lead_id={row['lead_id']}#lead-{row['lead_id']}", use_container_width=True)
        if row['email']:
            a2.link_button("📧 Email", f"mailto:{row['email']}", use_container_width=True)
        else:
            a2.caption("No email")
        if row['website']:
            a3.link_button("🌐 Website", f"https://{row['website']}", use_container_width=True)
        else:
            a3.caption("No website")
        
        # Mini stats badge
        stats = []
        if row['evidence'] > 0: stats.append(f"📂{row['evidence']}")
        if row['drafts'] > 0: stats.append(f"📝{row['drafts']}")
        if row['outreach_events'] > 0: stats.append(f"📬{row['outreach_events']}")
        if stats:
            a4.caption(" | ".join(stats))
        else:
            a4.caption("—")

        # Show similar leads inline (if feature enabled)
        if st.session_state.get('show_similar_inline', False):
            with st.expander("🧠 Similar Leads", expanded=False):
                # We need search_model here, but it's defined later in the script...
                # Streamlit usually handles this if search_model is in the same run,
                # but let's be safe. We'll use the model from sidebar if available.
                m_key = st.session_state.get('search_model_key', "0.6B")
                similar = find_similar_leads(row['lead_id'], limit=3, model_key=m_key)
                if not similar.empty:
                    for _, s in similar.iterrows():
                        sim_icon = "🟢" if s['similarity'] > 0.8 else "🟡" if s['similarity'] > 0.6 else "⚪"
                        st.markdown(f"{sim_icon} [{s['name']}](?lead_id={s['lead_id']}) — **{s['similarity']:.2f}**")
                else:
                    st.caption("No similar leads found")

@st.cache_data(ttl=300)
def load_all_embeddings(doc_type='profile_markdown', model_key="0.6B"):
    """Load all embeddings of a type into a matrix (cached)."""
    # Prefer pre-synced vector cache for specific models if available
    cache_file = Path(__file__).parent / ".cache" / "embeddings" / f"vectors_{model_key}.npz"
    
    if cache_file.exists():
        data = np.load(cache_file)
        # Verify dimension consistency (optional but good)
        return data['ids'], data['matrix'], data['dim']

    # If not in cache, load from DB with specific model filter
    # Map friendly names to DB patterns
    model_patterns = {"0.6B": "%0.6B%", "4B": "%4B%"}
    pattern = model_patterns.get(model_key, "%")
    
    query = """
        SELECT lead_id, vector_blob, embedding_dim 
        FROM leadops_vector_embeddings 
        WHERE doc_type = ? AND embedding_model LIKE ?
        ORDER BY lead_id ASC
    """
    df = pd.read_sql_query(query, conn, params=[doc_type, pattern])
    
    if df.empty:
        return None, None, None
    
    # Use actual dim from data
    dim = df.iloc[0]['embedding_dim']
    # Ensure all rows match this dim
    df = df[df['embedding_dim'] == dim]
    
    n = len(df)
    matrix = np.zeros((n, dim), dtype=np.float32)
    lead_ids = np.zeros(n, dtype=np.int32)
    
    for i, row in enumerate(df.itertuples()):
        matrix[i] = blob_to_vector(row.vector_blob, dim)
        lead_ids[i] = row.lead_id
    
    return lead_ids, matrix, dim

def find_similar_leads(lead_id, doc_type='profile_markdown', limit=10, model_key="0.6B"):
    """Find leads with similar embeddings using fast numpy."""
    lead_ids, matrix, dim = load_all_embeddings(doc_type, model_key=model_key)
    if matrix is None:
        return pd.DataFrame()
    
    # Find index of the query lead
    try:
        query_idx = np.where(lead_ids == int(lead_id))[0][0]
        query_vec = matrix[query_idx]
    except (IndexError, ValueError):
        # Fallback if lead not in loaded matrix
        query_vec = get_embedding_for_lead(lead_id, doc_type)
        if query_vec is None:
            return pd.DataFrame()
    
    # Normalize vectors for cosine similarity (with zero-vector protection)
    query_norm_val = np.linalg.norm(query_vec)
    if query_norm_val == 0:
        return pd.DataFrame()  # Zero vector has no meaningful similarity
    query_norm = query_vec / query_norm_val
    
    matrix_norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    # Replace zero-norm rows with small epsilon to avoid division by zero
    matrix_norms = np.maximum(matrix_norms, 1e-10)
    matrix_norm = matrix / matrix_norms
    
    # Compute all similarities at once
    similarities = np.dot(matrix_norm, query_norm)
    
    # Exclude self
    mask = lead_ids != int(lead_id)
    sims = similarities[mask]
    ids = lead_ids[mask]
    
    # Get top N
    top_idx = np.argsort(sims)[-limit:][::-1]
    top_ids = ids[top_idx]
    top_sims = sims[top_idx]
    
    # Build result
    sim_df = pd.DataFrame({'lead_id': top_ids.astype(int), 'similarity': top_sims})
    
    # Join with lead info
    # Using parameterized IN clause to avoid issues
    placeholders = ",".join(["?"] * len(top_ids))
    leads_df = pd.read_sql_query(
        f"SELECT lead_id, name, status, outreach_status, website FROM leadops_leads WHERE lead_id IN ({placeholders})",
        conn, params=top_ids.astype(int).tolist()
    )
    
    return leads_df.merge(sim_df, on='lead_id').sort_values('similarity', ascending=False)

# ─────────────────────────────────────────────────────────────────────────────
# Query Parser
# ─────────────────────────────────────────────────────────────────────────────

def parse_query(query_text):
    """
    Parse structured query syntax into SQL WHERE clauses.
    """
    if not query_text.strip():
        return None, None, None
    
    clauses = []
    params = []
    
    # Pattern: field:value or field:"multi word" or field>value or field<value
    pattern = r'(\b\w+)([:<>])(?:"([^"]+)"|(\S+))'
    
    # We'll build a list of what parts of the string were "consumed" by filters
    matches = list(re.finditer(pattern, query_text))
    consumed_indices = []
    
    valid_fields = {
        'status', 'outreach_status', 'email', 'email_domain', 'phone', 
        'website', 'website_domain', 'batch', 'name', 'contact_form',
        'disqualified', 'source', 'outreach_channel', 'audience_type',
        'audience_family', 'audience_subtype', 'business_operating_status',
        'lead_id'
    }
    
    for match in matches:
        field = match.group(1).lower()
        op = match.group(2)
        value = match.group(3) or match.group(4)
        
        if field == 'has':
            if value.lower() in valid_fields:
                clauses.append(f"{value.lower()} IS NOT NULL AND {value.lower()} != ''")
                consumed_indices.append((match.start(), match.end()))
            continue
        
        if field in valid_fields:
            if op == ':':
                clauses.append(f"{field} = ?")
                params.append(value)
            elif op == '>':
                clauses.append(f"{field} > ?")
                params.append(value)
            elif op == '<':
                clauses.append(f"{field} < ?")
                params.append(value)
            consumed_indices.append((match.start(), match.end()))

    # Extract remaining text for FTS (anything NOT in consumed_indices)
    free_text_parts = []
    last_idx = 0
    for start, end in sorted(consumed_indices):
        free_text_parts.append(query_text[last_idx:start])
        last_idx = end
    free_text_parts.append(query_text[last_idx:])
    
    free_text = " ".join(" ".join(free_text_parts).split()).strip()
    
    fts_query = None
    if free_text:
        # Standardize for FTS5 syntax (simple version)
        # We wrap in double quotes to handle spaces if multiple words, 
        # or just pass as is for porter stemming to work
        fts_query = free_text.replace('"', '') # Basic cleanup
    
    where_sql = " AND ".join(clauses) if clauses else None
    return where_sql, params, fts_query


# ─────────────────────────────────────────────────────────────────────────────
# Views / Queries
# ─────────────────────────────────────────────────────────────────────────────

QUICK_VIEWS = {
    "🧠 Similarity Explorer": "similarity_explorer",  # Special mode
    "All Leads": """
        SELECT lead_id, name, status, outreach_status, email, website, batch
        FROM leadops_leads 
        ORDER BY lead_id DESC
    """,
    "Ready to Send": """
        SELECT lead_id, name, send_priority, primary_send_hook, email, website
        FROM leadops_v_send_now
    """,
    "Needs Research": """
        SELECT lead_id, name, status, outreach_status, website
        FROM leadops_v_needs_research
        LIMIT 500
    """,
    "Sent (Awaiting Reply)": """
        SELECT lead_id, name, outreach_status, email, last_outreach_event_at
        FROM leadops_leads 
        WHERE outreach_status = 'sent'
        ORDER BY last_outreach_event_at DESC
    """,
    "Drafted": """
        SELECT lead_id, name, outreach_status, email
        FROM leadops_leads 
        WHERE outreach_status = 'drafted'
    """,
    "Bounced": """
        SELECT lead_id, name, email, email_domain
        FROM leadops_leads 
        WHERE outreach_status = 'bounced'
    """,
    "Replied!": """
        SELECT lead_id, name, email, last_outreach_event_at
        FROM leadops_leads 
        WHERE outreach_status = 'replied'
    """,
    "Disqualified": """
        SELECT lead_id, name, disqualified, status
        FROM leadops_leads 
        WHERE disqualified = 1
        ORDER BY lead_id DESC
        LIMIT 500
    """,
    "Duplicate Clusters": """
        SELECT cluster_id, cluster_key, member_count, member_names_json
        FROM leadops_entity_clusters
        WHERE member_count > 1
        ORDER BY member_count DESC
    """,
    "By Audience Type": """
        SELECT audience_family, audience_type, audience_subtype, COUNT(*) as cnt
        FROM leadops_v_audience_classification
        GROUP BY audience_family, audience_type, audience_subtype
        ORDER BY cnt DESC
    """,
}

# ─────────────────────────────────────────────────────────────────────────────
# Sidebar
# ─────────────────────────────────────────────────────────────────────────────

st.sidebar.title("🔍 LeadOps")
st.sidebar.caption("Query your lead database")

@st.cache_data(ttl=300)
def get_dashboard_stats():
    """Load and cache global dashboard stats."""
    return pd.read_sql_query("""
        SELECT 
            (SELECT COUNT(*) FROM leadops_leads) as total,
            (SELECT COUNT(*) FROM leadops_v_send_now) as ready,
            (SELECT COUNT(*) FROM leadops_v_needs_research) as research,
            (SELECT COUNT(*) FROM leadops_leads WHERE outreach_status='sent') as sent,
            (SELECT COUNT(*) FROM leadops_leads WHERE outreach_status='replied') as replied
    """, conn).iloc[0]

# Stats
st.sidebar.markdown("### Stats")
try:
    stats = get_dashboard_stats()
    
    st.sidebar.metric("Total Leads", f"{stats['total']:,}")
    st.sidebar.metric("Ready to Send", stats['ready'])
    st.sidebar.metric("Need Research", f"{stats['research']:,}")
    st.sidebar.metric("Sent", stats['sent'])
    st.sidebar.metric("Replied 🎉", stats['replied'])
except Exception as e:
    st.sidebar.error(f"Error loading stats: {e}")

# Vector/Embedding Options
st.sidebar.markdown("### 🧠 Vector Search")
search_model = st.sidebar.selectbox("Model", ["0.6B", "4B"], index=0, key="search_model_key", help="0.6B is faster, 4B is more precise")
show_inline_sim = st.sidebar.checkbox("Show similar leads on cards", value=False, key="show_similar_inline")

# Show cache sync status
cache_file = Path(__file__).parent / ".cache" / "embeddings" / f"vectors_{search_model}.npz"
if cache_file.exists():
    st.sidebar.caption(f"✅ Cache ready ({search_model})")
else:
    st.sidebar.caption(f"⚠️ No cache for {search_model}")

# Quick Views
st.sidebar.markdown("### Quick Views")
selected_view = st.sidebar.selectbox(
    "Choose a view", 
    [""] + list(QUICK_VIEWS.keys()),
    label_visibility="collapsed"
)

# Audience Filters
st.sidebar.markdown("### Audience Filters")
selected_family = ""
selected_type = ""
try:
    audience_families = pd.read_sql_query(
        "SELECT DISTINCT audience_family FROM leadops_v_audience_classification WHERE audience_family IS NOT NULL ORDER BY audience_family",
        conn
    )['audience_family'].tolist()
    
    selected_family = st.sidebar.selectbox(
        "Family",
        [""] + audience_families,
        label_visibility="collapsed",
        key="audience_family_filter"
    )
    
    if selected_family:
        audience_types = pd.read_sql_query(
            f"SELECT DISTINCT audience_type FROM leadops_v_audience_classification WHERE audience_family = ? ORDER BY audience_type",
            conn, params=[selected_family]
        )['audience_type'].tolist()
        
        selected_type = st.sidebar.selectbox(
            "Type",
            [""] + audience_types,
            label_visibility="collapsed",
            key="audience_type_filter"
        )
        
        if selected_type:
            st.sidebar.caption(f"Filtering: {selected_family} → {selected_type}")
except (sqlite3.Error, KeyError) as e:
    # Audience classification view may not exist or be malformed
    st.sidebar.caption("⚠️ Audience filters unavailable")

# Link to network visualization
st.sidebar.markdown("---")
st.sidebar.markdown("### 🕸️ Network View")
st.sidebar.page_link("pages/1_Network_View.py", label="🌌 Open Network View", use_container_width=True)

# Maintenance
st.sidebar.markdown("---")
st.sidebar.markdown("### 🛠️ Maintenance")

# Check for cache sync issues
try:
    m_key = st.session_state.get('search_model_key', "0.6B")
    model_patterns = {"0.6B": "%0.6B%", "4B": "%4B%"}
    pattern = model_patterns.get(m_key, "%")
    
    db_count = pd.read_sql_query(
        "SELECT COUNT(*) as cnt FROM leadops_vector_embeddings WHERE doc_type = 'profile_markdown' AND embedding_model LIKE ?",
        conn, params=[pattern]
    ).iloc[0]['cnt']
    
    cache_file = Path(__file__).parent / ".cache" / "embeddings" / f"vectors_{m_key}.npz"
    if cache_file.exists():
        data = np.load(cache_file)
        cache_count = len(data['ids'])
        if db_count != cache_count:
            st.sidebar.warning(f"⚠️ Cache out of sync!\nDB: {db_count} | Cache: {cache_count}")
            if st.sidebar.button("🔄 Resync Cache", help="Run sync_vector_cache.py to update"):
                with st.spinner("Syncing..."):
                    import subprocess
                    subprocess.run(["python", "sync_vector_cache.py"], check=True)
                    st.rerun()
    else:
        st.sidebar.info("📌 No vector cache found. Run sync to speed up search.")
except (sqlite3.Error, FileNotFoundError, OSError) as e:
    # Vector embeddings table or cache files may not exist
    pass  # Silently ignore - not critical for UI operation

if st.sidebar.button("🧹 Clear UI Cache"):
    st.cache_data.clear()
    st.cache_resource.clear()
    st.success("Cache cleared!")
    st.rerun()

# ─────────────────────────────────────────────────────────────────────────────
# Main Application
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=60, show_spinner=False)
def run_query(sql, params=None):
    """Run and cache SQL queries."""
    return pd.read_sql_query(sql, conn, params=params)

# ─────────────────────────────────────────────────────────────────────────────
# Main Application
# ─────────────────────────────────────────────────────────────────────────────

def main():
    st.title("🔍 Lead Database")

    # Initialize session state defaults
    if 'results_limit' not in st.session_state:
        st.session_state.results_limit = 50
    if 'saved_queries' not in st.session_state:
        st.session_state.saved_queries = [
            ("has:email status:ready", "Has email, ready"),
            ("outreach:uncontacted has:website", "Uncontacted with website"),
            ("audience_type:big_brand", "Big brands"),
        ]

    # Query input with AI mode indicator
    col1, col_toggle, col_clear = st.columns([6, 1, 1])
    with col1:
        query_input = st.text_input(
            "Query",
            placeholder='status:ready has:email OR just type to search...',
            label_visibility="collapsed",
            value=st.session_state.get('last_query', ""),
            help="Use field:value filters, has:field, or free text. Press Enter to search."
        )
        if query_input != st.session_state.get('last_query', ""):
            st.session_state.last_query = query_input
            st.session_state.results_limit = 50 # Reset on new query
    
    with col_toggle:
        semantic_mode = st.toggle("🧠 AI", value=False, help="Enable Semantic/Concept search using AI embeddings")
        # Show AI engine status when AI mode is on
        if semantic_mode:
            from leads_network import is_server_alive
            if is_server_alive():
                st.caption("🟢 Ready")
            else:
                st.caption("🔴 Offline")

    with col_clear:
        if st.button("🗑️ Clear", help="Reset to default view", use_container_width=True):
            st.session_state.last_query = ""
            st.session_state.results_limit = 50
            st.query_params.clear()
            st.rerun()

    # Query syntax help
    with st.expander("📖 Query Syntax", expanded=False):
        st.markdown("""
        **Filters (exact match):**
        - `status:ready` `outreach_status:sent` `email_domain:gmail.com` `batch:2500`

        **Comparison:**
        - `lead_id>1000` `lead_id<500`

        **Has filter (non-empty check):**
        - `has:email` `has:website` `has:phone`

        **Free text (LIKE search):**
        - `construction` `texas roofing` — searches name, email, website

        **Combine:**
        - `status:ready has:email construction` — all conditions must match

        **Quoted values:**
        - `name:"John Smith"` — exact phrase
        """)

    # Saved queries with one-click run
    with st.expander("📋 Saved Queries", expanded=False):
        st.caption("Click to run a pre-built query")
        for i, (q, label) in enumerate(st.session_state.saved_queries):
            c1, c2 = st.columns([4, 1])
            c1.markdown(f"**{label}**")
            c1.caption(f"`{q}`")
            if c2.button("▶️ Run", key=f"run_{i}", use_container_width=True):
                st.session_state.last_query = q
                st.session_state.results_limit = 50
                st.rerun()

    # Determine what to show
    results_df = None

    if query_input:
        if semantic_mode:
            # (Semantic code remains same...)
            from leads_network import get_query_embedding, is_server_alive
            
            if not is_server_alive():
                st.error("🧠 AI Search Engine is offline. Start it in the Network View.")
                st.stop()
            
            with st.spinner("Analyzing concept..."):
                query_vec = get_query_embedding(query_input)
                if query_vec is not None:
                    m_key = st.session_state.get('search_model_key', "0.6B")
                    lead_ids_cache, matrix, dim = load_all_embeddings(model_key=m_key)
                    
                    if matrix is not None:
                        # Normalize with zero-vector protection
                        query_norm_val = np.linalg.norm(query_vec)
                        if query_norm_val == 0:
                            st.error("Query produced zero vector - cannot compute similarity.")
                        else:
                            query_norm = query_vec / query_norm_val
                            matrix_norms = np.linalg.norm(matrix, axis=1, keepdims=True)
                            matrix_norms = np.maximum(matrix_norms, 1e-10)  # Prevent division by zero
                            matrix_norm = matrix / matrix_norms
                            similarities = np.dot(matrix_norm, query_norm)
                            top_idx = np.argsort(similarities)[-st.session_state.results_limit:][::-1]
                            top_ids = lead_ids_cache[top_idx]
                            top_sims = similarities[top_idx]
                            results_df = pd.DataFrame({'lead_id': top_ids.astype(int), 'similarity': top_sims})
                            st.caption(f"Found {len(results_df)} semantically similar leads")
                    else:
                        st.error("Embeddings matrix not found. Sync cache first.")
                else:
                    st.error("Failed to generate embedding for query.")
        else:
            # Standard structured OR high-performance FTS query
            where_sql, params, fts_query = parse_query(query_input)
            
            if fts_query:
                # Use FTS table for high performance
                # If we have structured filters (where_sql), we join them
                fts_where = "leadops_search_fts MATCH ?"
                if where_sql:
                    combined_sql = f"""
                        SELECT DISTINCT l.lead_id, l.name, l.status, l.outreach_status, l.email, l.website, l.batch, fts.title as snippet
                        FROM leadops_leads l
                        JOIN leadops_search_fts fts ON l.lead_id = fts.lead_id
                        WHERE {fts_where} AND {where_sql}
                        ORDER BY l.lead_id DESC
                        LIMIT ?
                    """
                    final_params = [fts_query] + params + [st.session_state.results_limit]
                else:
                    combined_sql = f"""
                        SELECT DISTINCT l.lead_id, l.name, l.status, l.outreach_status, l.email, l.website, l.batch, fts.title as snippet
                        FROM leadops_leads l
                        JOIN leadops_search_fts fts ON l.lead_id = fts.lead_id
                        WHERE {fts_where}
                        ORDER BY l.lead_id DESC
                        LIMIT ?
                    """
                    final_params = [fts_query, st.session_state.results_limit]
                
                try:
                    results_df = run_query(combined_sql, params=final_params)
                    st.caption(f"Found {len(results_df)} leads via FTS")
                except Exception as e:
                    st.error(f"FTS Query error: {e}")
            
            elif where_sql:
                # Structured only (no free text)
                base_sql = f"""
                    SELECT lead_id, name, status, outreach_status, email, website, batch
                    FROM leadops_leads
                    WHERE {where_sql}
                    ORDER BY lead_id DESC
                    LIMIT ?
                """
                try:
                    results_df = run_query(base_sql, params=params + [st.session_state.results_limit])
                    st.caption(f"Found {len(results_df)} leads")
                except Exception as e:
                    st.error(f"Query error: {e}")
            else:
                st.warning("No valid query terms")

    elif selected_view and selected_view in QUICK_VIEWS:
        # Quick view selected
        if selected_view == "Similarity Explorer":
            # Special: Pick a lead and show its similarity network
            st.markdown("### 🧠 Vector Similarity Explorer")
            st.caption("Discover leads with similar *concepts* — not just matching keywords. Uses AI embeddings to find semantic relationships.")
            
            # Add explanation
            with st.expander("ℹ️ How this works", expanded=False):
                st.markdown("""
                **Semantic Similarity** finds leads that are conceptually similar:
                - A "ranch equipment supplier" might match with "livestock feed store"
                - Same industry, different words
                - Powered by AI embeddings (vector similarity)
                
                **Tip:** Choose a lead with a rich profile for better matches.
                """)

            sample_leads = pd.read_sql_query("""
                SELECT lead_id, name FROM leadops_leads 
                WHERE lead_id IN (SELECT DISTINCT lead_id FROM leadops_vector_embeddings LIMIT 100)
                ORDER BY lead_id DESC LIMIT 20
            """, conn)

            anchor_lead = st.selectbox(
                "Pick a lead to explore similarities:",
                sample_leads['lead_id'].tolist(),
                format_func=lambda x: sample_leads[sample_leads['lead_id']==x]['name'].iloc[0]
            )

            if anchor_lead:
                with st.spinner("Loading embeddings & computing similarities..."):
                    similar = find_similar_leads(anchor_lead, 'profile_markdown', limit=st.session_state.results_limit, model_key=search_model)
                    if not similar.empty:
                        results_df = similar
                        st.success(f"Found {len(similar)} semantically similar leads")
                    else:
                        st.warning("No embeddings found for this lead")
        else:
            try:
                # Add LIMIT to quick views if they don't have one, or replace it
                view_sql = QUICK_VIEWS[selected_view]
                if "LIMIT" in view_sql.upper():
                    # Simple regex replace for existing limit
                    view_sql = re.sub(r"LIMIT \d+", f"LIMIT {st.session_state.results_limit}", view_sql, flags=re.IGNORECASE)
                else:
                    view_sql += f" LIMIT {st.session_state.results_limit}"
                
                results_df = pd.read_sql_query(view_sql, conn)
            except Exception as e:
                st.error(f"Error loading view: {e}")

    elif selected_family and selected_type:
        # Audience filter selected
        try:
            results_df = pd.read_sql_query("""
                SELECT l.lead_id, l.name, l.status, l.outreach_status, l.email, l.website
                FROM leadops_leads l
                JOIN leadops_v_audience_classification a ON l.lead_id = a.lead_id
                WHERE a.audience_family = ? AND a.audience_type = ?
                ORDER BY l.lead_id DESC
                LIMIT ?
            """, conn, params=[selected_family, selected_type, st.session_state.results_limit])
            st.caption(f"Showing {selected_family} → {selected_type}")
        except Exception as e:
            st.error(f"Error loading audience: {e}")

    else:
        # Default: show recent leads with helpful guidance
        results_df = pd.read_sql_query("""
            SELECT lead_id, name, status, outreach_status, email, website
            FROM leadops_leads
            ORDER BY lead_id DESC
            LIMIT ?
        """, conn, params=[st.session_state.results_limit])
        st.info(f"📋 Showing {st.session_state.results_limit} most recent leads. Enter a query above or select a Quick View to filter.")

    # Show results as cards
    if results_df is not None and not results_df.empty:
        # Result count with pagination hint
        result_count = len(results_df)
        if result_count >= 100:
            st.warning(f"⚠️ Showing **{result_count} results** (limited to 100). Refine your query for fewer results.")
        elif result_count >= 50:
            st.info(f"📊 **{result_count} results** — narrow your search for faster browsing")
        else:
            st.success(f"✅ **{result_count} results**")

        # Get lead IDs from results
        if 'lead_id' in results_df.columns:
            lead_ids = results_df['lead_id'].tolist()
        elif 'id' in results_df.columns:
            lead_ids = results_df['id'].tolist()
        else:
            lead_ids = []

        # Enrich with metadata (show progress for large sets)
        if len(lead_ids) > 20:
            with st.spinner(f"Enriching {len(lead_ids)} leads..."):
                enriched = enrich_leads(lead_ids)
        else:
            enriched = enrich_leads(lead_ids)

        # Merge snippets/similarity if present
        if 'snippet' in results_df.columns:
            enriched = enriched.merge(results_df[['lead_id', 'snippet']], on='lead_id', how='left')
        if 'similarity' in results_df.columns:
            enriched = enriched.merge(results_df[['lead_id', 'similarity']], on='lead_id', how='left')

        # Render cards
        for _, row in enriched.iterrows():
            snippet = row.get('snippet')
            similarity = row.get('similarity')
            render_lead_card(row, snippet=snippet, similarity=similarity)

        # Export section
        st.markdown("---")
        col_load, col_export1, col_export2 = st.columns([2, 2, 1])

        with col_load:
            if result_count >= st.session_state.results_limit:
                remaining = result_count - st.session_state.results_limit
                if st.button(f"➕ Load More ({remaining} remaining)", use_container_width=True):
                    st.session_state.results_limit += 50
                    st.rerun()
            else:
                st.success("✅ All results loaded")

        with col_export1:
            st.caption(f"📄 Export {result_count} leads as CSV")
        with col_export2:
            csv = enriched.to_csv(index=False).encode('utf-8')
            st.download_button(
                "⬇️ Download CSV",
                csv,
                file_name=f"leads_export_{result_count}.csv",
                mime="text/csv",
                type="secondary"
            )
    elif results_df is not None and results_df.empty:
        # Empty state
        st.info("🔍 No leads found. Try adjusting your query or selecting a different view.")

    # ─────────────────────────────────────────────────────────────────────────────
    # Lead Detail View (via ?lead_id= param)
    # ─────────────────────────────────────────────────────────────────────────────

    lead_id_param = st.query_params.get("lead_id") or st.query_params.get("id")
    
    # Validate lead_id is a valid integer
    try:
        lead_id_int = int(lead_id_param) if lead_id_param else None
    except (ValueError, TypeError):
        st.error(f"Invalid lead ID: '{lead_id_param}'")
        lead_id_int = None
    
    if lead_id_int:
        # Back navigation with context preservation
        col_back, col_title = st.columns([1, 4])
        with col_back:
            if st.button("← Back to List", type="secondary", use_container_width=True):
                # Only remove the lead detail parameters, keeping search/query state
                if "lead_id" in st.query_params:
                    del st.query_params["lead_id"]
                if "id" in st.query_params:
                    del st.query_params["id"]
                st.rerun()
        with col_title:
            st.markdown(f"## Lead #{lead_id_int}")

        st.markdown("---")

        # Core lead info
        lead = pd.read_sql_query(
            "SELECT * FROM leadops_leads WHERE lead_id = ?",
            conn, params=[lead_id_int]
        )

        if lead.empty:
            st.error("Lead not found")
        else:
            lead = lead.iloc[0]

            # Status header with badges
            status_colors = {"ready": "🟢", "complete": "✅", "disqualified": "🔴", "draft-prepared": "📝", "researching": "🔍", "new": "✨"}
            outreach_colors = {"sent": "📨", "replied": "🎉", "bounced": "💥", "drafted": "📝", "uncontacted": "⏳"}
            
            status_icon = status_colors.get(str(lead['status']).lower(), '⚪')
            st.markdown(f"## {status_icon} {lead['name']}")
            
            # Status badges row
            badge_col1, badge_col2, badge_col3, badge_col4 = st.columns(4)
            badge_col1.metric("Status", str(lead.get('status', 'N/A')))
            outreach_val = str(lead.get('outreach_status', 'N/A'))
            badge_col2.metric("Outreach", outreach_colors.get(outreach_val.lower(), '❓') + " " + outreach_val)
            badge_col3.metric("Batch", str(lead.get('batch', 'N/A') or '—'))
            badge_col4.metric("Source", str(lead.get('source', 'N/A') or '—'))
            
            # Quick action buttons - consolidated row
            st.markdown("---")
            action_col1, action_col2, action_col3, action_col4 = st.columns(4)
            if lead['email']:
                action_col1.link_button("📧 Email Lead", f"mailto:{lead['email']}", use_container_width=True, type="primary")
            else:
                action_col1.caption("No email")
            if lead['website']:
                action_col2.link_button("🌐 Open Website", f"https://{lead['website']}", use_container_width=True)
            else:
                action_col2.caption("No website")
            # Google search link
            search_query = str(lead['name']).replace('"', '')
            action_col3.link_button("🔍 Google Search", f"https://www.google.com/search?q={search_query}", use_container_width=True)
            # Network view link
            action_col4.page_link("pages/1_Network_View.py", label="🌌 Network View", use_container_width=True)

            st.markdown("---")

            # Contact info card
            st.markdown("#### 📞 Contact Information")
            contact_col1, contact_col2 = st.columns(2)
            with contact_col1:
                if lead['email']:
                    st.markdown(f"📧 **Email:** [{lead['email']}](mailto:{lead['email']})")
                if lead['phone']:
                    st.markdown(f"📱 **Phone:** {lead['phone']}")
                if lead['website']:
                    st.markdown(f"🌐 **Website:** [{lead['website_domain'] or lead['website']}](https://{lead['website']})")
            with contact_col2:
                if lead['address']:
                    st.markdown(f"📍 **Address:** {lead['address']}")
                if lead.get('contact_form'):
                    st.markdown(f"📝 **Contact Form:** {lead['contact_form']}")
                if lead.get('disqualified'):
                    st.error("⚠️ This lead has been disqualified")

            # Consolidated Metadata Fetch
            with st.spinner("Loading metadata..."):
                metadata = {}
                # Artifacts
                metadata['artifacts'] = pd.read_sql_query(
                    "SELECT id, artifact_kind, artifact_group, storage_path, size_bytes, modified_at FROM leadops_evidence_artifacts WHERE lead_id = ?",
                    conn, params=[lead_id_int]
                )
                # Events
                metadata['events'] = pd.read_sql_query(
                    "SELECT * FROM leadops_outreach_events WHERE lead_id = ? ORDER BY occurred_at DESC",
                    conn, params=[lead_id_int]
                )
                # Embeddings
                metadata['embeddings'] = pd.read_sql_query(
                    "SELECT doc_id, doc_type, embedding_model, embedding_dim, indexed_at FROM leadops_vector_embeddings WHERE lead_id = ?",
                    conn, params=[lead_id_int]
                )
                # Cluster
                metadata['cluster'] = pd.read_sql_query("""
                    SELECT ec.cluster_id, ec.cluster_key, ec.member_count, ec.member_names_json
                    FROM leadops_entity_clusters ec
                    JOIN leadops_leads l ON l.name IN (SELECT value FROM json_each(ec.member_names_json))
                    WHERE l.lead_id = ?
                    LIMIT 1
                """, conn, params=[lead_id_int])

            # Profile
            profile = pd.read_sql_query(
                "SELECT * FROM leadops_profiles WHERE lead_id = ?",
                conn, params=[lead_id_int]
            )
            
            if not profile.empty:
                st.markdown("---")
                st.markdown("### Profile")
                profile = profile.iloc[0]
                
                # Raw markdown profile
                if profile.get('raw_markdown'):
                    st.markdown(profile['raw_markdown'])
                
                # Key extracted fields
                with st.expander("📋 Basic Fields"):
                    for field in ['title', 'address', 'naics', 'decision_maker', 'outreach_angle', 'next_steps']:
                        if profile.get(field):
                            st.markdown(f"**{field}:** {profile[field]}")
                
                # Extended profile sections
                extended_fields = [
                    ('business_overview', 'Business Overview'),
                    ('contact_decision_makers', 'Decision Makers'),
                    ('contact_information', 'Contact Info'),
                    ('online_presence', 'Online Presence'),
                    ('market_position', 'Market Position'),
                    ('opportunity_assessment', 'Opportunity'),
                    ('website_presence', 'Website'),
                    ('audit_highlights', 'Audit Highlights'),
                    ('security_trust', 'Security & Trust'),
                    ('ux_conversion', 'UX/Conversion'),
                    ('performance_tech', 'Performance/Tech'),
                    ('google_business_profile', 'Google Business'),
                    ('social_presence', 'Social Presence'),
                    ('observations', 'Observations'),
                ]
                for field, label in extended_fields:
                    val = profile.get(field)
                    if val and str(val).strip():
                        with st.expander(f"📄 {label}"):
                            st.markdown(str(val))
            
            # Drafts
            drafts = pd.read_sql_query(
                "SELECT uid, subject, draft_date, body_text FROM leadops_drafts WHERE lead_id = ?",
                conn, params=[lead_id_int]
            )
            if not drafts.empty:
                st.markdown("---")
                st.markdown(f"### Drafts ({len(drafts)})")
                for _, d in drafts.iterrows():
                    with st.expander(f"📧 {d['subject']} ({d['draft_date']})"):
                        st.text(d['body_text'][:500] + "..." if len(str(d['body_text'])) > 500 else d['body_text'])
            
            # Outreach events
            events = metadata['events']
            if not events.empty:
                st.markdown("---")
                st.markdown(f"### Outreach History ({len(events)})")
                st.dataframe(events, use_container_width=True, hide_index=True)
            
            # Evidence artifacts
            artifacts = metadata['artifacts']
            if not artifacts.empty:
                st.markdown("---")
                st.markdown(f"### Evidence ({len(artifacts)})")
                
                for _, art in artifacts.iterrows():
                    with st.expander(f"📁 {art['artifact_kind']} - {art['artifact_group']} ({art['id']})"):
                        # Check if file exists and try to preview
                        if art['storage_path']:
                            # SECURITY: Sanitize path to prevent path traversal
                            raw_path = str(art['storage_path'])
                            # Block path traversal attempts
                            if '..' in raw_path or raw_path.startswith('/') or ':' in raw_path.split('/')[0] if '/' in raw_path else ':' in raw_path.split('\\')[0] if '\\' in raw_path else False:
                                st.error("🚫 Invalid file path - potential security violation")
                            else:
                                art_path = Path(raw_path)
                                # Handle relative paths if needed (assuming they are relative to a root)
                                if not art_path.exists():
                                    # Try common locations
                                    potential_path = Path(__file__).parent / art_path
                                    if potential_path.exists():
                                        art_path = potential_path
                                
                                # SECURITY: Resolve and verify path is within allowed directory
                                try:
                                    resolved_path = art_path.resolve()
                                    base_dir = Path(__file__).parent.resolve()
                                    if not str(resolved_path).startswith(str(base_dir)):
                                        st.error("🚫 File access denied - path outside allowed directory")
                                    elif art_path.exists():
                                        st.caption(f"📍 `{art_path.name}` ({art['size_bytes']/1024:.1f} KB)")
                                        ext = art_path.suffix.lower()
                                        if ext in ['.png', '.jpg', '.jpeg', '.webp']:
                                            st.image(str(art_path), use_container_width=True)
                                        elif ext in ['.txt', '.md', '.json', '.html']:
                                            try:
                                                with open(art_path, 'r', encoding='utf-8') as f:
                                                    content = f.read()
                                                    # SECURITY: Limit file size to prevent memory exhaustion
                                                    if len(content) > 100000:
                                                        st.warning(f"File too large ({len(content)} chars). Showing first 100K.")
                                                        content = content[:100000]
                                                    if ext == '.json':
                                                        st.json(content)
                                                    else:
                                                        st.code(content, language=ext[1:] if ext != '.md' else 'markdown')
                                            except Exception as e:
                                                st.error(f"Could not read content: {e}")
                                        else:
                                            st.info("Preview not available for this file type.")
                                    else:
                                        st.warning(f"File not found on disk: `{art_path.name}`")
                                except (OSError, ValueError) as path_err:
                                    st.error(f"Invalid file path: {path_err}")
                        
                        st.dataframe(pd.DataFrame([art]), hide_index=True)
            
            # Embeddings
            embeddings = metadata['embeddings']
            if not embeddings.empty:
                st.markdown("---")
                st.markdown(f"### Embeddings ({len(embeddings)})")
                st.dataframe(embeddings, use_container_width=True, hide_index=True)
            
            # Entity cluster (duplicates)
            cluster = metadata['cluster']
            if not cluster.empty:
                st.markdown("---")
                st.markdown("### Entity Cluster (Potential Duplicates)")
                try:
                    names = json.loads(cluster.iloc[0]['member_names_json'])
                    for name in names:
                        # Escape to prevent XSS
                        st.markdown(f"- {escape_markdown(name)}")
                except (json.JSONDecodeError, KeyError, TypeError):
                    st.warning("Could not parse cluster member names")

            # Similar leads (using embeddings)
            st.markdown("---")
            st.markdown("### 🧠 Similar Leads (Semantic Search)")
            st.caption("Find leads with similar profiles, not just matching keywords")

            doc_types = pd.read_sql_query(
                "SELECT DISTINCT doc_type FROM leadops_vector_embeddings WHERE lead_id = ?",
                conn, params=[lead_id_int]
            )

            if not doc_types.empty:
                doc_type_options = doc_types['doc_type'].tolist()

                col_doc, col_btn, col_limit = st.columns([2, 1, 1])
                with col_doc:
                    selected_doc = st.selectbox("Compare by:", doc_type_options, key=f"sim_doc_{lead_id_int}", label_visibility="collapsed")
                with col_limit:
                    sim_limit = st.number_input("Limit", min_value=5, max_value=50, value=15, key=f"sim_limit_{lead_id_int}", label_visibility="collapsed")
                with col_btn:
                    if st.button("🔍 Find Similar", key=f"find_sim_{lead_id_int}", type="primary", use_container_width=True):
                        with st.spinner("Computing similarities..."):
                            # Use search_model from sidebar
                            m_key = st.session_state.get('search_model_key', "0.6B")
                            similar = find_similar_leads(lead_id_int, selected_doc, limit=sim_limit, model_key=m_key)
                            if not similar.empty:
                                st.success(f"Found {len(similar)} similar leads")
                                # Render as clickable cards
                                for _, s in similar.iterrows():
                                    sim_score = s['similarity']
                                    sim_color = "🟢" if sim_score > 0.8 else "🟡" if sim_score > 0.6 else "⚪"
                                    st.markdown(f"{sim_color} **{sim_score:.2f}** — [{s['name']}](?lead_id={s['lead_id']}) — {s['status']}")
                            else:
                                st.warning("No similar leads found.")
            else:
                st.info("📌 No embeddings available for this lead. Run profile generation first.")

    # Sidebar footer - only show on main list view (not detail view)
    if not lead_id_int:
        st.sidebar.markdown("---")
        st.sidebar.markdown("### ⌨️ Tips")
        st.sidebar.caption("• Click any lead name to view details")
        st.sidebar.caption("• Use `?` to match any single character")
        st.sidebar.caption("• Use `*` to match multiple characters")
        st.sidebar.caption("• Combine filters: `status:ready email:*@gmail.com`")
        st.sidebar.markdown("---")
        st.sidebar.caption(f"📂 DB: {DB_PATH.name}")
        st.sidebar.caption(f"🔄 Last refresh: {pd.Timestamp.now().strftime('%H:%M:%S')}")
        st.sidebar.markdown("---")
        st.sidebar.caption("⚡ **Performance:** Queries and enrichments are cached (1-5 min) to keep the UI snappy.")
